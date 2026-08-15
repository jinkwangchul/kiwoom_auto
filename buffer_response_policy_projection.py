# -*- coding: utf-8 -*-
"""Read-only buffer-response policy projection.

Production combines the persisted canonical settings with already projected
snapshots.  The widget reader remains only for editor/test snapshots and is
never the Production Source of Truth.  This module owns no persistence,
runtime mutation, close command, cancellation, or order execution path.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Mapping

from gui_operation_environment import (
    read_buffer_response_policy,
    validate_buffer_response_policy,
)


MODE_UNIFIED = "UNIFIED"
MODE_SEGMENTED = "SEGMENTED"

SEGMENT_PROFIT = "PROFIT"
SEGMENT_LOSS = "LOSS"

EFFECTIVE_EARLY_CLOSE = "EARLY_CLOSE"
EFFECTIVE_IMMEDIATE_LIQUIDATION_REQUIRED = "IMMEDIATE_LIQUIDATION_REQUIRED"

_FACTOR_FIELD_BY_LABEL = {
    "손익금액": "cumulative_profit",
    "손익비율": "cumulative_rate",
    "투입금액": "open_cost",
}
_DIRECTION_BY_LABEL = {
    "높은순": "DESCENDING",
    "낮은순": "ASCENDING",
}
_RESPONSE_MODE_BY_LABEL = {
    "조기마감": "EARLY_CLOSE",
    "즉시청산": "IMMEDIATE_LIQUIDATION",
    "구간마감": "BUFFER_ENTRY_THRESHOLD",
}
_STRATEGY_KEY_BY_MODE_SEGMENT = {
    (MODE_UNIFIED, ""): "unified",
    (MODE_SEGMENTED, SEGMENT_PROFIT): "profit",
    (MODE_SEGMENTED, SEGMENT_LOSS): "loss",
}
_ALLOWED_THRESHOLDS = frozenset(range(10, 100, 10))


def _text(value: object) -> str:
    return str(value or "").strip()


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _unavailable(reason: str, **context: object) -> dict[str, object]:
    result: dict[str, object] = {
        "available": False,
        "applicable": False,
        "application_mode": "",
        "selected_segment": "",
        "evaluation_factor": "",
        "evaluation_field": "",
        "direction": "",
        "sort_direction": "",
        "configured_threshold": None,
        "buffer_entry_ratio": None,
        "configured_response_mode": "",
        "configured_response_label": "",
        "effective_response": "",
        "total_cumulative_profit": None,
        "candidate_factor_values": {},
        "reason": _text(reason) or "BUFFER_RESPONSE_POLICY_UNAVAILABLE",
    }
    result.update(context)
    return result


def _current_text(widget: object) -> str:
    reader = getattr(widget, "currentText", None)
    if not callable(reader):
        return ""
    return _text(reader())


def read_buffer_response_settings(surface: object) -> dict[str, object]:
    """Read an editor snapshot only; never use it as Production policy."""
    if surface is None:
        return _unavailable("BUFFER_RESPONSE_SETTINGS_SURFACE_MISSING")
    visibility_reader = getattr(surface, "isVisible", None)
    if not callable(visibility_reader) or not bool(visibility_reader()):
        return _unavailable("BUFFER_RESPONSE_SETTINGS_SURFACE_NOT_OPEN")

    mode_reader = getattr(surface, "application_mode", None)
    if not callable(mode_reader):
        return _unavailable("BUFFER_RESPONSE_APPLICATION_MODE_UNAVAILABLE")
    mode = _text(mode_reader()).upper()
    if mode not in {MODE_UNIFIED, MODE_SEGMENTED}:
        return _unavailable("BUFFER_RESPONSE_APPLICATION_MODE_INVALID")

    ratio_combo = getattr(surface, "buffer_close_ratio_combo", None)
    ratio_text = _current_text(ratio_combo)
    if not ratio_text.endswith("%"):
        return _unavailable("BUFFER_RESPONSE_THRESHOLD_INVALID")
    try:
        threshold = int(ratio_text[:-1].strip())
    except ValueError:
        return _unavailable("BUFFER_RESPONSE_THRESHOLD_INVALID")
    if threshold not in _ALLOWED_THRESHOLDS:
        return _unavailable("BUFFER_RESPONSE_THRESHOLD_INVALID")

    rows = getattr(surface, "strategy_rows", None)
    badges = getattr(surface, "strategy_action_badges", None)
    if not isinstance(rows, Mapping) or not isinstance(badges, Mapping):
        return _unavailable("BUFFER_RESPONSE_STRATEGY_WIDGETS_UNAVAILABLE")

    strategies: dict[str, dict[str, str]] = {}
    required_keys = ("unified",) if mode == MODE_UNIFIED else ("profit", "loss")
    for key in required_keys:
        row = rows.get(key)
        badge = badges.get(key)
        if not isinstance(row, (list, tuple)) or len(row) != 1:
            return _unavailable("BUFFER_RESPONSE_STRATEGY_ROW_INVALID")
        controls = row[0]
        if not isinstance(controls, (list, tuple)) or len(controls) != 2:
            return _unavailable("BUFFER_RESPONSE_STRATEGY_ROW_INVALID")
        factor = _current_text(controls[0])
        direction = _current_text(controls[1])
        badge_text_reader = getattr(badge, "text", None)
        response = _text(badge_text_reader()) if callable(badge_text_reader) else ""
        if factor not in _FACTOR_FIELD_BY_LABEL:
            return _unavailable("BUFFER_RESPONSE_EVALUATION_FACTOR_INVALID")
        if direction not in _DIRECTION_BY_LABEL:
            return _unavailable("BUFFER_RESPONSE_DIRECTION_INVALID")
        if response not in _RESPONSE_MODE_BY_LABEL:
            return _unavailable("BUFFER_RESPONSE_MODE_INVALID")
        strategies[key] = {
            "evaluation_factor": factor,
            "direction": direction,
            "response_mode": response,
        }

    return {
        "available": True,
        "application_mode": mode,
        "configured_threshold": threshold,
        "strategies": strategies,
        "reason": "",
    }


def _persisted_settings(
    settings_policy: Mapping[str, object] | object,
) -> dict[str, object]:
    if not isinstance(settings_policy, Mapping):
        return _unavailable("BUFFER_RESPONSE_POLICY_NOT_CONFIGURED")
    if settings_policy.get("available") is False:
        return _unavailable(
            _text(settings_policy.get("reason"))
            or "BUFFER_RESPONSE_POLICY_UNAVAILABLE"
        )
    try:
        normalized = validate_buffer_response_policy(settings_policy)
    except ValueError:
        return _unavailable("BUFFER_RESPONSE_POLICY_MALFORMED")
    return {
        "available": True,
        "application_mode": normalized["application_mode"],
        "configured_threshold": normalized["threshold_percent"],
        "strategies": normalized["strategies"],
        "reason": "",
    }


def _project_pnl_inputs(
    pnl_by_stock: Mapping[str, Mapping[str, object]] | object,
) -> tuple[Decimal, dict[str, Mapping[str, object]]] | None:
    if not isinstance(pnl_by_stock, Mapping) or not pnl_by_stock:
        return None
    total_profit = Decimal("0")
    normalized: dict[str, Mapping[str, object]] = {}
    for raw_code, raw_projection in pnl_by_stock.items():
        code = _text(raw_code).lstrip("A")
        if not code or not isinstance(raw_projection, Mapping):
            return None
        if raw_projection.get("available") is not True:
            return None
        cumulative_profit = _decimal(raw_projection.get("cumulative_profit"))
        if cumulative_profit is None:
            return None
        total_profit += cumulative_profit
        normalized[code] = raw_projection
    return total_profit, normalized


def project_buffer_response_policy(
    *,
    pnl_by_stock: Mapping[str, Mapping[str, object]] | object,
    budget_activity: Mapping[str, object] | object,
    settings_policy: Mapping[str, object] | object | None = None,
    settings_surface: object | None = None,
) -> dict[str, object]:
    """Project the effective response without executing or persisting it."""
    if settings_policy is not None:
        settings = _persisted_settings(settings_policy)
    elif settings_surface is not None:
        # Compatibility for editor-focused tests only. Production coordinators
        # always pass the result of the canonical persisted-policy reader.
        settings = read_buffer_response_settings(settings_surface)
    else:
        settings = _persisted_settings(read_buffer_response_policy())
    if settings.get("available") is not True:
        return _unavailable(_text(settings.get("reason")))

    if not isinstance(budget_activity, Mapping) or budget_activity.get("available") is not True:
        return _unavailable("BUFFER_BUDGET_ACTIVITY_UNAVAILABLE")
    entry_amount = _decimal(budget_activity.get("entry_amount"))
    entry_ratio = _decimal(budget_activity.get("entry_ratio"))
    if entry_amount is None or entry_ratio is None:
        return _unavailable("BUFFER_ENTRY_PROJECTION_UNAVAILABLE")
    if entry_amount <= 0:
        return _unavailable(
            "BUFFER_NOT_ENTERED",
            buffer_entry_ratio=entry_ratio,
            configured_threshold=settings.get("configured_threshold"),
        )
    if entry_ratio < 0:
        return _unavailable("BUFFER_ENTRY_RATIO_INVALID")

    pnl_projection = _project_pnl_inputs(pnl_by_stock)
    if pnl_projection is None:
        return _unavailable("CONFIRMABLE_PNL_SNAPSHOT_UNAVAILABLE")
    total_profit, normalized_pnl = pnl_projection

    mode = _text(settings.get("application_mode")).upper()
    selected_segment = ""
    if mode == MODE_SEGMENTED:
        selected_segment = SEGMENT_PROFIT if total_profit >= 0 else SEGMENT_LOSS
    strategy_key = _STRATEGY_KEY_BY_MODE_SEGMENT.get((mode, selected_segment))
    strategies = settings.get("strategies")
    if not strategy_key or not isinstance(strategies, Mapping):
        return _unavailable("BUFFER_RESPONSE_STRATEGY_SELECTION_INVALID")
    strategy = strategies.get(strategy_key)
    if not isinstance(strategy, Mapping):
        return _unavailable("BUFFER_RESPONSE_STRATEGY_SELECTION_INVALID")

    factor = _text(strategy.get("evaluation_factor"))
    factor_field = _FACTOR_FIELD_BY_LABEL.get(factor, "")
    direction = _text(strategy.get("direction"))
    response_label = _text(strategy.get("response_mode"))
    configured_response = _RESPONSE_MODE_BY_LABEL.get(response_label, "")
    if not factor_field or direction not in _DIRECTION_BY_LABEL or not configured_response:
        return _unavailable("BUFFER_RESPONSE_STRATEGY_SELECTION_INVALID")

    candidate_values: dict[str, Decimal] = {}
    for code, projection in sorted(normalized_pnl.items()):
        value = _decimal(projection.get(factor_field))
        if value is None:
            return _unavailable(
                "BUFFER_RESPONSE_FACTOR_VALUE_UNAVAILABLE",
                application_mode=mode,
                selected_segment=selected_segment,
                evaluation_factor=factor,
                evaluation_field=factor_field,
            )
        candidate_values[code] = value

    threshold = int(settings["configured_threshold"])
    if configured_response == "EARLY_CLOSE":
        effective_response = EFFECTIVE_EARLY_CLOSE
    elif configured_response == "IMMEDIATE_LIQUIDATION":
        effective_response = EFFECTIVE_IMMEDIATE_LIQUIDATION_REQUIRED
    else:
        effective_response = (
            EFFECTIVE_EARLY_CLOSE
            if entry_ratio < Decimal(threshold)
            else EFFECTIVE_IMMEDIATE_LIQUIDATION_REQUIRED
        )

    return {
        "available": True,
        "applicable": True,
        "application_mode": mode,
        "selected_segment": selected_segment,
        "evaluation_factor": factor,
        "evaluation_field": factor_field,
        "direction": direction,
        "sort_direction": _DIRECTION_BY_LABEL[direction],
        "configured_threshold": threshold,
        "buffer_entry_ratio": entry_ratio,
        "configured_response_mode": configured_response,
        "configured_response_label": response_label,
        "effective_response": effective_response,
        "total_cumulative_profit": total_profit,
        "candidate_factor_values": candidate_values,
        "reason": "",
    }
