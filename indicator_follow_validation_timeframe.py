# -*- coding: utf-8 -*-
"""Validation-only timeframe identity for Indicator Follow chart/replay."""

from __future__ import annotations

from typing import Any, Mapping

from historical_candle_row_normalizer import normalize_date_based_candle_row


MINUTE_VALUES = (1, 3, 5, 10, 15, 30, 60, 120, 240)
_MINUTE_SUFFIX = "\ubd84"
_PERIOD_IDENTITIES = {
    "D1": {"kind": "DAY", "label": "\uc77c"},
    "W1": {"kind": "WEEK", "label": "\uc8fc"},
    "MO1": {"kind": "MONTH", "label": "\uc6d4"},
    "Y1": {"kind": "YEAR", "label": "\ub144"},
}
TIMEFRAME_LABELS = tuple(
    f"{value}{_MINUTE_SUFFIX}" for value in MINUTE_VALUES
) + tuple(identity["label"] for identity in _PERIOD_IDENTITIES.values())
_PERIOD_KEYS = {
    identity["label"]: key for key, identity in _PERIOD_IDENTITIES.items()
}


def normalize_validation_timeframe(value: Any, *, fallback_minutes: int = 5) -> dict[str, Any]:
    """Return a stable Validation timeframe identity without faking minute values."""
    if isinstance(value, Mapping):
        raw_key = str(value.get("key") or "").strip().upper()
        raw_label = str(value.get("label") or "").strip()
        raw_minutes = value.get("minutes")
        if raw_key in _PERIOD_IDENTITIES:
            identity = _PERIOD_IDENTITIES[raw_key]
            return {
                "key": raw_key,
                "kind": identity["kind"],
                "minutes": None,
                "label": identity["label"],
            }
        if raw_key.startswith("M"):
            try:
                minute = int(raw_key[1:])
            except (TypeError, ValueError):
                minute = None
            if minute in MINUTE_VALUES:
                return {
                    "key": f"M{minute}",
                    "kind": "MINUTE",
                    "minutes": minute,
                    "label": f"{minute}{_MINUTE_SUFFIX}",
                }
        if raw_label:
            return normalize_validation_timeframe(raw_label, fallback_minutes=fallback_minutes)
        if raw_minutes is not None:
            return normalize_validation_timeframe(raw_minutes, fallback_minutes=fallback_minutes)

    text = str(value or "").strip()
    upper_text = text.upper()
    if upper_text in _PERIOD_IDENTITIES:
        identity = _PERIOD_IDENTITIES[upper_text]
        return {
            "key": upper_text,
            "kind": identity["kind"],
            "minutes": None,
            "label": identity["label"],
        }
    if upper_text.startswith("M"):
        try:
            minute = int(upper_text[1:])
        except (TypeError, ValueError):
            minute = None
        if minute in MINUTE_VALUES:
            return {
                "key": f"M{minute}",
                "kind": "MINUTE",
                "minutes": minute,
                "label": f"{minute}{_MINUTE_SUFFIX}",
            }
    if text in _PERIOD_KEYS:
        key = _PERIOD_KEYS[text]
        identity = _PERIOD_IDENTITIES[key]
        return {
            "key": key,
            "kind": identity["kind"],
            "minutes": None,
            "label": identity["label"],
        }
    if text.endswith(_MINUTE_SUFFIX):
        text = text[:-1].strip()
    try:
        minute = int(float(text))
    except (TypeError, ValueError):
        minute = None
    if minute in MINUTE_VALUES:
        return {
            "key": f"M{minute}",
            "kind": "MINUTE",
            "minutes": minute,
            "label": f"{minute}{_MINUTE_SUFFIX}",
        }

    fallback = fallback_minutes if fallback_minutes in MINUTE_VALUES else 5
    return {
        "key": f"M{fallback}",
        "kind": "MINUTE",
        "minutes": fallback,
        "label": f"{fallback}{_MINUTE_SUFFIX}",
    }


def validation_timeframe_from_ui_state(
    ui_state: Mapping[str, Any] | None,
    *,
    fallback_minutes: int = 5,
) -> dict[str, Any]:
    source = ui_state if isinstance(ui_state, Mapping) else {}
    basic = source.get("basic") if isinstance(source.get("basic"), Mapping) else {}
    return normalize_validation_timeframe(
        basic.get("basic_signal_interval_combo"),
        fallback_minutes=fallback_minutes,
    )


def validation_timeframe_from_rules(
    rules: Mapping[str, Any] | None,
    *,
    ui_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = rules if isinstance(rules, Mapping) else {}
    bar = source.get("bar") if isinstance(source.get("bar"), Mapping) else {}
    fallback_raw = bar.get("bar_minutes", source.get("bar_minutes", 5))
    try:
        fallback = int(fallback_raw)
    except (TypeError, ValueError):
        fallback = 5
    if isinstance(ui_state, Mapping):
        return validation_timeframe_from_ui_state(ui_state, fallback_minutes=fallback)
    configured = source.get("validation_timeframe")
    if isinstance(configured, Mapping):
        return normalize_validation_timeframe(configured, fallback_minutes=fallback)
    return normalize_validation_timeframe(fallback, fallback_minutes=fallback)


def validation_timeframe_for_request(
    rules: Mapping[str, Any] | None,
    request_minutes: int,
) -> dict[str, Any]:
    source = rules if isinstance(rules, Mapping) else {}
    configured = source.get("validation_timeframe")
    if isinstance(configured, Mapping):
        return normalize_validation_timeframe(
            configured,
            fallback_minutes=request_minutes,
        )
    return normalize_validation_timeframe(
        request_minutes,
        fallback_minutes=request_minutes,
    )


def timeframe_cache_token(timeframe: Mapping[str, Any] | str) -> str:
    identity = normalize_validation_timeframe(timeframe)
    return str(identity["key"])


def timeframe_display_label(timeframe: Mapping[str, Any] | str) -> str:
    return str(normalize_validation_timeframe(timeframe)["label"])


def normalize_validation_period_row(
    row: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    """Project one date-based broker row into Validation's candle row shape."""
    return normalize_date_based_candle_row(row)
