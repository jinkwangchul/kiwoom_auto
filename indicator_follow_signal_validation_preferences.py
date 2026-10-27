"""V2-only chart preference persistence."""

from __future__ import annotations

import json
from typing import Any, Mapping

from PyQt5.QtCore import QSettings

PREFERENCES_SETTINGS_KEY = "ui/signal_validation_v2/preferences"
_SCHEMA = "indicator_follow_signal_validation_preferences"
_VERSION = 1

_REPEAT_TO_UI = {"ROUND": "회차기준", "BUDGET": "예산기준", "ACTIVE_BUY": "능동매수"}
_REPEAT_FROM_UI = {value: key for key, value in _REPEAT_TO_UI.items()}
_ROUND_TO_UI = {"ADD": "+", "MULTIPLY": "x"}
_ROUND_FROM_UI = {value: key for key, value in _ROUND_TO_UI.items()}
_DIRECTION_TO_UI = {"UP": "상향", "DOWN": "하향", "BOTH": "상하"}
_DIRECTION_FROM_UI = {value: key for key, value in _DIRECTION_TO_UI.items()}
_COMPARE_TO_UI = {">=": "이상", "<=": "이하", "WITHIN": "이내", "OUTSIDE": "이탈"}
_COMPARE_FROM_UI = {value: key for key, value in _COMPARE_TO_UI.items()}


def _default_settings() -> QSettings:
    return QSettings(QSettings.IniFormat, QSettings.UserScope, "jinkwangchul", "kiwoom_auto")


def _payload(raw: object) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _number(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _integer(value: object, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


class IndicatorFollowSignalValidationPreferenceStore:
    """Own the single V2 QSettings preference document."""

    def __init__(self, *, settings: object | None = None) -> None:
        self._settings = settings if settings is not None else _default_settings()

    def load(self) -> dict[str, Any]:
        source = _payload(self._settings.value(PREFERENCES_SETTINGS_KEY, ""))
        execution = source.get("validation_execution")
        market = source.get("validation_market_scope")
        display = source.get("filter_display")
        execution = execution if isinstance(execution, Mapping) else {}
        market = market if isinstance(market, Mapping) else {}
        display = display if isinstance(display, Mapping) else {}

        if not source:
            return {}
        return {
            "averaging_enabled": execution.get("enabled") is True,
            "first_buy_quantity": str(_integer(execution.get("first_buy_quantity"), 1)),
            "repeat_mode": _REPEAT_TO_UI.get(str(execution.get("repeat_mode") or "").upper(), "회차기준"),
            "round_operator": _ROUND_TO_UI.get(str(execution.get("round_operator") or "").upper(), "+"),
            "round_value": str(_number(execution.get("round_budget_value"), 0.5)),
            "budget_ratio": str(_number(execution.get("budget_ratio"), 2.0)),
            "active_direction": _DIRECTION_TO_UI.get(str(execution.get("active_direction") or "").upper(), "상향"),
            "active_ratio": str(_number(execution.get("active_ratio"), 0.45)),
            "active_compare": _COMPARE_TO_UI.get(str(execution.get("active_compare") or "").upper(), "이상"),
            "trading_cost_enabled": execution.get("trading_cost_enabled") is True,
            "trading_cost_percent": str(_number(execution.get("trading_cost_percent"), 0.2)),
            "regular_market_only": market.get("regular_market_only") is True,
            "filter_display_enabled": display.get("enabled") is not False,
        }

    def save(self, values: Mapping[str, Any]) -> None:
        execution = {
            "enabled": bool(values.get("averaging_enabled", False)),
            "first_buy_quantity": _integer(values.get("first_buy_quantity"), 1),
            "repeat_mode": _REPEAT_FROM_UI.get(str(values.get("repeat_mode") or ""), "ROUND"),
            "round_operator": _ROUND_FROM_UI.get(str(values.get("round_operator") or ""), "ADD"),
            "round_budget_value": _number(values.get("round_value"), 0.5),
            "budget_ratio": _number(values.get("budget_ratio"), 2.0),
            "active_direction": _DIRECTION_FROM_UI.get(str(values.get("active_direction") or ""), "UP"),
            "active_ratio": _number(values.get("active_ratio"), 0.45),
            "active_compare": _COMPARE_FROM_UI.get(str(values.get("active_compare") or ""), ">="),
            "trading_cost_enabled": bool(values.get("trading_cost_enabled", False)),
            "trading_cost_percent": _number(values.get("trading_cost_percent"), 0.2),
        }

        payload = {
            "schema": _SCHEMA,
            "version": _VERSION,
            "validation_execution": execution,
            "validation_market_scope": {
                "regular_market_only": bool(values.get("regular_market_only", False)),
            },
            "filter_display": {
                "enabled": bool(values.get("filter_display_enabled", True)),
            },
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._settings.setValue(PREFERENCES_SETTINGS_KEY, serialized)
        sync = getattr(self._settings, "sync", None)
        if callable(sync):
            sync()
