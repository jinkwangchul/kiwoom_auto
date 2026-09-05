# -*- coding: utf-8 -*-
"""Preview-only mapper from indicator-follow UI state to engine rules.

This module never writes rules.json. It only returns a copied preview dict and
warnings for values that are not safe to map yet.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from math import isfinite
from typing import Any

from engines.condition_engine import parse_condition_expression


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def build_ui_state_hash(ui_state: dict[str, Any]) -> str:
    payload = json.dumps(
        ui_state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _apply_preview_hash_payload(apply_preview: dict[str, Any]) -> dict[str, Any]:
    preview = _as_dict(apply_preview)
    return {
        "applied_rules_preview": deepcopy(_as_dict(preview.get("applied_rules_preview"))),
        "applied_patches": deepcopy(_as_list(preview.get("applied_patches"))),
        "skipped_patches": deepcopy(_as_list(preview.get("skipped_patches"))),
        "summary": deepcopy(_as_dict(preview.get("summary"))),
    }


def build_apply_preview_hash(apply_preview: dict[str, Any]) -> str:
    """Return a stable hash for the deterministic commit-relevant apply preview subset."""
    return _stable_hash(_apply_preview_hash_payload(apply_preview))


_MISSING = object()
BAR_MINUTES_PATH = "bar.bar_minutes"
SIGNAL_RUNTIME_POLICY_PATH = "signal_runtime_policy"
BUY_CONDITIONS_PATH = "buy.groups[0].conditions"
BUY_MOVING_AVERAGE_FILTER_PATH = "buy.filters.moving_average"
BUY_PRICE_COMPARE_FILTER_PATH = "buy.filters.price_compare"
BUY_BOLLINGER_FILTER_PATH = "buy.filters.bollinger"
BUY_OCR_FILTER_PATH = "buy.filters.ocr"
BUY_RSI_FILTER_PATH = "buy.filters.rsi"
BUY_COMPOSITE_FILTER_PATH = "buy.filters.composite"
BUY_EXECUTION_BASE_PATH = "buy.execution.base"
BUY_EXECUTION_REPEAT_PATH = "buy.execution.repeat"
BUY_EXECUTION_ADDITIONAL_PATH = "buy.execution.additional"
BUY_EXECUTION_CYCLE_PATH = "buy.execution.cycle"
_BUY_EXECUTION_PATHS = {
    BUY_EXECUTION_BASE_PATH,
    BUY_EXECUTION_REPEAT_PATH,
    BUY_EXECUTION_ADDITIONAL_PATH,
    BUY_EXECUTION_CYCLE_PATH,
}
_BUY_EXECUTION_CANDIDATE_KEYS = {
    BUY_EXECUTION_BASE_PATH: "base",
    BUY_EXECUTION_REPEAT_PATH: "repeat",
    BUY_EXECUTION_ADDITIONAL_PATH: "additional",
    BUY_EXECUTION_CYCLE_PATH: "cycle",
}
P1_EXECUTION_LOCK_REASON = "MAPPED_BUT_EXECUTION_NOT_CONNECTED"
CYCLE_OPTION_EXECUTION_LOCK_REASON = "CYCLE_OPTION_EXECUTION_NOT_CONNECTED"
RSI_INDICATOR_PATH = "indicators.rsi"
SELL_METHOD_SELECTED_SETS_PATH = "sell.method.selected_sets"
SELL_METHOD_SETTING_A_PATH = "sell.method.setting_a"
SELL_METHOD_SETTING_B_PATH = "sell.method.setting_b"
SELL_METHOD_SETTING_C_PATH = "sell.method.setting_c"
_SELL_METHOD_PATHS = {
    SELL_METHOD_SELECTED_SETS_PATH,
    SELL_METHOD_SETTING_A_PATH,
    SELL_METHOD_SETTING_B_PATH,
    SELL_METHOD_SETTING_C_PATH,
}
_SELL_METHOD_SETTING_PATHS = {
    "setting_a": SELL_METHOD_SETTING_A_PATH,
    "setting_b": SELL_METHOD_SETTING_B_PATH,
    "setting_c": SELL_METHOD_SETTING_C_PATH,
}
SELL_CONDITION_A_SIGNAL_PREVIEW_PATH = "sell.signals.ui_preview_condition_a"
APPROVED_SELL_CONDITION_A_SIGNAL_KEY = "ui_condition_a"
SELL_CONDITION_A_SIGNAL_TARGET_PATH = f"sell.signals.{APPROVED_SELL_CONDITION_A_SIGNAL_KEY}"
SELL_CONDITION_B_SIGNAL_PREVIEW_PATH = "sell.signals.ui_preview_condition_b"
APPROVED_SELL_CONDITION_B_SIGNAL_KEY = "ui_condition_b"
SELL_CONDITION_B_SIGNAL_TARGET_PATH = f"sell.signals.{APPROVED_SELL_CONDITION_B_SIGNAL_KEY}"
SELL_CONDITION_C_SIGNAL_PREVIEW_PATH = "sell.signals.ui_preview_condition_c"
APPROVED_SELL_CONDITION_C_SIGNAL_KEY = "ui_condition_c"
SELL_CONDITION_C_SIGNAL_TARGET_PATH = f"sell.signals.{APPROVED_SELL_CONDITION_C_SIGNAL_KEY}"
SELL_MACD_SIGNAL_PREVIEW_PATH = "sell.signals.ui_preview_condition_c_macd_sell"
APPROVED_SELL_MACD_SIGNAL_KEY = "ui_condition_c_macd_sell"
SELL_MACD_SIGNAL_TARGET_PATH = f"sell.signals.{APPROVED_SELL_MACD_SIGNAL_KEY}"
SELL_PROFIT_RATE_SIGNAL_PATH = "sell.signals.profit_rate_sell"
_SELL_PROFIT_RATE_ALLOWED_FIELDS = {
    "enabled",
    "profit_rate_percent",
    "basis",
}
_SELL_ADD_SIGNAL_TARGETS = {
    SELL_CONDITION_A_SIGNAL_PREVIEW_PATH: (
        SELL_CONDITION_A_SIGNAL_TARGET_PATH,
        APPROVED_SELL_CONDITION_A_SIGNAL_KEY,
    ),
    SELL_CONDITION_B_SIGNAL_PREVIEW_PATH: (
        SELL_CONDITION_B_SIGNAL_TARGET_PATH,
        APPROVED_SELL_CONDITION_B_SIGNAL_KEY,
    ),
    SELL_CONDITION_C_SIGNAL_PREVIEW_PATH: (
        SELL_CONDITION_C_SIGNAL_TARGET_PATH,
        APPROVED_SELL_CONDITION_C_SIGNAL_KEY,
    ),
    SELL_MACD_SIGNAL_PREVIEW_PATH: (
        SELL_MACD_SIGNAL_TARGET_PATH,
        APPROVED_SELL_MACD_SIGNAL_KEY,
    ),
}
_RULE_CANDIDATE_DECISIONS = {
    "PENDING",
    "APPROVED",
    "REJECTED",
    "DEFERRED",
    "APPLIED_PREVIEW_ONLY",
}


def _get_path_value(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if "[" in part and part.endswith("]"):
            name, index_text = part[:-1].split("[", 1)
            if not isinstance(current, dict) or name not in current:
                return _MISSING
            current = current[name]
            try:
                index = int(index_text)
            except ValueError:
                return _MISSING
            if not isinstance(current, list) or index < 0 or index >= len(current):
                return _MISSING
            current = current[index]
        else:
            if not isinstance(current, dict) or part not in current:
                return _MISSING
            current = current[part]
    return current


def _build_signal_runtime_policy_candidate(basic: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if "basic_duplicate_signal_combo" not in basic and "basic_error_policy_combo" not in basic:
        return None
    duplicate = {
        "선행신호 우선": "LEADING",
        "후행신호 우선": "TRAILING",
        "LEADING": "LEADING",
        "TRAILING": "TRAILING",
    }.get(str(basic.get("basic_duplicate_signal_combo") or "후행신호 우선").strip())
    error = {
        "매매중지": "STOP_AND_REVIEW",
        "매매지속": "CONTINUE_NEXT_CYCLE",
        "STOP_AND_REVIEW": "STOP_AND_REVIEW",
        "CONTINUE_NEXT_CYCLE": "CONTINUE_NEXT_CYCLE",
    }.get(str(basic.get("basic_error_policy_combo") or "매매중지").strip())
    if duplicate is None or error is None:
        warnings.append("signal runtime policy is invalid")
        return None
    return {
        "path": SIGNAL_RUNTIME_POLICY_PATH,
        "value": {
            "duplicate_priority": duplicate,
            "error_policy": error,
            "normal_duplicate_cancel_is_error": False,
        },
    }


def _preview_diff_risk(path: str) -> str:
    if path == SELL_CONDITION_A_SIGNAL_PREVIEW_PATH:
        return "low"
    if path == SELL_CONDITION_B_SIGNAL_PREVIEW_PATH:
        return "low"
    if path == SELL_CONDITION_C_SIGNAL_PREVIEW_PATH:
        return "low"
    if path == SELL_MACD_SIGNAL_PREVIEW_PATH:
        return "low"
    if path == "sell.signals.macd_sell":
        return "high"
    if path == RSI_INDICATOR_PATH:
        return "low"
    if path == BUY_MOVING_AVERAGE_FILTER_PATH:
        return "low"
    if path == BUY_PRICE_COMPARE_FILTER_PATH:
        return "low"
    if path == BUY_OCR_FILTER_PATH:
        return "low"
    if path == BUY_RSI_FILTER_PATH:
        return "low"
    if path == BUY_COMPOSITE_FILTER_PATH:
        return "low"
    if path in _BUY_EXECUTION_PATHS:
        return "medium"
    if path in _SELL_METHOD_PATHS:
        return "medium"
    if path in {"buy.groups", BUY_CONDITIONS_PATH}:
        return "medium"
    return "low"


def _preview_diff_note(path: str) -> str:
    notes = {
        BAR_MINUTES_PATH: "UI preview candidate from basic signal interval.",
        BUY_CONDITIONS_PATH: (
            "UI preview-only merge candidate for current buy.groups[0].conditions."
        ),
        RSI_INDICATOR_PATH: (
            "UI preview-only RSI indicator candidate using the existing indicators.rsi structure."
        ),
        BUY_MOVING_AVERAGE_FILTER_PATH: (
            "UI preview-only BUY current-price/MA60 filter candidate."
        ),
        BUY_PRICE_COMPARE_FILTER_PATH: (
            "UI preview-only BUY price-compare filter candidate."
        ),
        BUY_BOLLINGER_FILTER_PATH: (
            "UI preview-only BUY current-price/Bollinger filter candidate."
        ),
        BUY_OCR_FILTER_PATH: (
            "UI preview-only BUY OCR/OSC filter candidate."
        ),
        BUY_RSI_FILTER_PATH: (
            "UI preview-only BUY RSI filter candidate."
        ),
        BUY_COMPOSITE_FILTER_PATH: (
            "UI preview-only BUY composite filter candidate."
        ),
        BUY_EXECUTION_BASE_PATH: (
            "UI preview-only BUY execution base policy candidate."
        ),
        BUY_EXECUTION_REPEAT_PATH: (
            "UI preview-only BUY execution repeat policy candidate."
        ),
        BUY_EXECUTION_ADDITIONAL_PATH: (
            "BUY additional policy connected to the Routine-owned P2 execution consumer."
        ),
        BUY_EXECUTION_CYCLE_PATH: (
            "Signal-scoped BUY cycle policy; unsupported CANCEL_BATCH remains execution-locked."
        ),
        SELL_METHOD_SELECTED_SETS_PATH: (
            "UI preview-only SELL method selected sets policy candidate."
        ),
        SELL_METHOD_SETTING_A_PATH: (
            "UI preview-only SELL method setting A policy candidate."
        ),
        SELL_METHOD_SETTING_B_PATH: (
            "UI preview-only SELL method setting B policy candidate."
        ),
        SELL_METHOD_SETTING_C_PATH: (
            "UI preview-only SELL method setting C policy candidate."
        ),
        SELL_PROFIT_RATE_SIGNAL_PATH: (
            "UI preview-only profit_rate_sell set_signal candidate."
        ),
        "sell.signals.macd_sell": (
            "UI preview-only sell MACD condition candidate; does not replace existing rules."
        ),
        SELL_MACD_SIGNAL_PREVIEW_PATH: (
            "UI preview-only add signal candidate; existing sell.signals.macd_sell is unchanged."
        ),
        SELL_CONDITION_A_SIGNAL_PREVIEW_PATH: (
            "UI preview-only condition A add signal candidate; existing sell.signals.macd_sell is unchanged."
        ),
        SELL_CONDITION_B_SIGNAL_PREVIEW_PATH: (
            "UI preview-only condition B add signal candidate; existing sell.signals.macd_sell is unchanged."
        ),
        SELL_CONDITION_C_SIGNAL_PREVIEW_PATH: (
            "UI preview-only condition C add signal candidate; existing sell.signals.macd_sell is unchanged."
        ),
    }
    return notes.get(path, "UI preview candidate path.")


def _safe_int(value: Any) -> int | None:
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _compare_operator(text: Any) -> str | None:
    value = str(text or "").strip()
    mapping = {
        "\uc774\uc0c1": ">=",
        ">=": ">=",
        "\uc774\ud558": "<=",
        "<=": "<=",
        "\ucd08\uacfc": ">",
        ">": ">",
        "\ubbf8\ub9cc": "<",
        "<": "<",
    }
    return mapping.get(value)


def _direct_compare_operator(text: Any) -> str | None:
    value = str(text or "").strip()
    mapping = {
        "\uc774\uc0c1": ">=",
        ">=": ">=",
        "\uc774\ud558": "<=",
        "<=": "<=",
        "\ucd08\uacfc": ">",
        ">": ">",
        "\ubbf8\ub9cc": "<",
        "<": "<",
        "\ub3cc\ud30c": "CROSS_UP",
    }
    return mapping.get(value)


def _series_target(text: Any) -> str | None:
    value = str(text or "").strip().upper()
    mapping = {
        "": None,
        "\ud604\uc7ac\uac00": "CLOSE",
        "\uc885\uac00": "CLOSE",
        "CURRENT": "CLOSE",
        "CURRENT_PRICE": "CLOSE",
        "CLOSE": "CLOSE",
        "\uc8fc\ubb38\uac00": "ORDER_PRICE",
        "ORDER": "ORDER_PRICE",
        "ORDER_PRICE": "ORDER_PRICE",
        "\ud3c9\ub2e8\uac00": "AVG_PRICE",
        "AVERAGE_PRICE": "AVG_PRICE",
        "AVG_PRICE": "AVG_PRICE",
    }
    return mapping.get(value, value if value else None)


def _signed_float(sign: Any, value: Any) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    return -abs(number) if str(sign or "").strip() == "-" else abs(number)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _build_buy_osc_conditions(signal_filter: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    raw_threshold = signal_filter.get("buy_ocr_value_line")
    if raw_threshold in (None, ""):
        return conditions

    turn_text = str(signal_filter.get("buy_ocr_turn_combo", "")).strip()
    if turn_text:
        turn_operator = {
            "\uc0c1\uc2b9": "TURN_UP",
            "\ud558\ub77d": "TURN_DOWN",
        }.get(turn_text)
        if turn_operator is None:
            warnings.append(f"buy OCR turn is not mapped: {turn_text!r}")
        else:
            conditions.append({
                "enabled": True,
                "not": False,
                "target": "OSC",
                "operator": turn_operator,
                "description": "UI preview: buy OCR/OSC turn condition",
            })

    compare_operator = _compare_operator(signal_filter.get("buy_ocr_compare_combo"))
    threshold = _signed_float(
        signal_filter.get("buy_ocr_sign_combo"),
        raw_threshold,
    )
    if compare_operator and threshold is not None:
        conditions.append({
            "enabled": True,
            "not": False,
            "target": "OSC",
            "operator": compare_operator,
            "value": threshold,
            "description": "UI preview: buy OCR/OSC threshold condition",
        })
    elif signal_filter.get("buy_ocr_value_line") not in (None, ""):
        warnings.append("buy OCR threshold is not fully mapped")

    if signal_filter.get("buy_ocr_bar_line") not in (None, "", "0"):
        warnings.append("buy OCR bar offset is not supported by the current condition engine")

    return conditions


def _build_buy_ocr_filter_candidate(signal_filter: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if "buy_ocr_enabled" in signal_filter and not _truthy_ui(signal_filter.get("buy_ocr_enabled")):
        return None

    raw_threshold = signal_filter.get("buy_ocr_value_line")
    if raw_threshold in (None, ""):
        return None

    conditions = _build_buy_osc_conditions(signal_filter, warnings)
    if not conditions:
        warnings.append("buy OCR filter candidate was not generated")
        return None

    return {
        "path": BUY_OCR_FILTER_PATH,
        "value": {
            "enabled": True,
            "conditions_logic": "AND",
            "conditions": conditions,
        },
    }


def _build_buy_rsi_filter_candidate(signal_filter: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if "buy_rsi_enabled" in signal_filter and not _truthy_ui(signal_filter.get("buy_rsi_enabled")):
        return None

    raw_period = signal_filter.get("buy_rsi_period_line")
    raw_threshold = signal_filter.get("buy_rsi_value_line")
    if raw_period in (None, "") or raw_threshold in (None, ""):
        return None

    period = _safe_int(raw_period)
    operator = _compare_operator(signal_filter.get("buy_rsi_compare_combo"))
    threshold = _safe_float(raw_threshold)
    if period is None:
        warnings.append("buy RSI period is not numeric")
        return None
    if operator is None:
        warnings.append(f"buy RSI compare is not mapped: {signal_filter.get('buy_rsi_compare_combo')!r}")
        return None
    if threshold is None:
        warnings.append("buy RSI threshold is not numeric")
        return None

    return {
        "path": BUY_RSI_FILTER_PATH,
        "value": {
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "operator": operator,
                "threshold": threshold,
                "period": period,
            }],
        },
    }


def _build_buy_composite_filter_candidate(signal_filter: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    signal_expression = str(signal_filter.get("signal_expression") or "").strip()
    if signal_expression:
        parsed = parse_condition_expression(
            signal_expression,
            allowed_identifiers={"A", "B", "C", "D"},
            allow_duplicate_identifiers=True,
        )
        if not parsed.get("ok"):
            warnings.append(f"buy signal expression is invalid: {parsed.get('reason')}")
            return None
        return {
            "path": BUY_COMPOSITE_FILTER_PATH,
            "value": {
                "enabled": True,
                "expression": {
                    "source": signal_expression,
                    "normalized": parsed["normalized"],
                    "ast": parsed["ast"],
                    "identifiers": parsed["identifiers"],
                    "identifier_map": {
                        "A": "ocr",
                        "B": "bollinger",
                        "C": "moving_average",
                        "D": "rsi",
                    },
                },
                "include_unreferenced_active_filters": "AND_REQUIRED",
                "groups": [],
            },
        }

    if "buy_composite" not in signal_filter:
        return None

    source = signal_filter.get("buy_composite")
    if not isinstance(source, dict):
        warnings.append("buy composite config is not a dict")
        return None

    enabled = _truthy_ui(source.get("enabled"))
    logic = str(source.get("logic", "AND") or "").strip().upper()
    if logic not in {"AND", "OR"}:
        warnings.append(f"buy composite logic is not supported: {source.get('logic')!r}")
        return None

    include_policy = str(source.get("include_unreferenced_active_filters", "AND_REQUIRED") or "").strip().upper()
    if include_policy != "AND_REQUIRED":
        warnings.append(f"buy composite include policy is not supported: {source.get('include_unreferenced_active_filters')!r}")
        return None

    groups = source.get("groups")
    if not isinstance(groups, list):
        warnings.append("buy composite groups is not a list")
        return None

    supported_filters = {"rsi", "moving_average", "price_compare", "bollinger", "ocr"}
    normalized_groups: list[dict[str, Any]] = []
    active_group_count = 0
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            warnings.append(f"buy composite group {index + 1} is not a dict")
            return None

        group_enabled = _truthy_ui(group.get("enabled"))
        group_logic = str(group.get("logic", "AND") or "").strip().upper()
        if group_logic not in {"AND", "OR"}:
            warnings.append(f"buy composite group {index + 1} logic is not supported: {group.get('logic')!r}")
            return None

        filters = group.get("filters")
        if not isinstance(filters, list):
            warnings.append(f"buy composite group {index + 1} filters is not a list")
            return None

        normalized_filters: list[str] = []
        seen_filters: set[str] = set()
        for filter_name in filters:
            name = str(filter_name or "").strip()
            if name not in supported_filters:
                warnings.append(f"buy composite group {index + 1} filter is not supported: {filter_name!r}")
                return None
            if name in seen_filters:
                warnings.append(f"buy composite group {index + 1} has duplicate filter: {name}")
                return None
            seen_filters.add(name)
            normalized_filters.append(name)

        if group_enabled:
            active_group_count += 1
            if not normalized_filters:
                warnings.append(f"buy composite group {index + 1} active filters is empty")
                return None

        normalized_groups.append({
            "enabled": group_enabled,
            "logic": group_logic,
            "filters": normalized_filters,
        })

    if enabled and active_group_count == 0:
        warnings.append("buy composite has no active groups")
        return None

    return {
        "path": BUY_COMPOSITE_FILTER_PATH,
        "value": {
            "enabled": enabled,
            "logic": logic,
            "include_unreferenced_active_filters": include_policy,
            "groups": normalized_groups,
        },
    }


def _truthy_ui(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on", "checked", "enabled", "사용", "활성"}


def _optional_filter_enabled(values: dict[str, Any], enabled_key: str, value_key: str) -> bool:
    if enabled_key in values:
        return _truthy_ui(values.get(enabled_key))
    return values.get(value_key) not in (None, "")


def _build_buy_ma_filter_candidate(signal_filter: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if not _optional_filter_enabled(signal_filter, "buy_ma_enabled", "buy_ma_value_line"):
        return None

    period = _safe_int(signal_filter.get("buy_ma_value_line"))
    if period is None or period <= 0:
        warnings.append("buy MA period is not numeric")
        return None

    direction = str(signal_filter.get("buy_ma_direction_combo") or "").strip()
    compare_text = str(signal_filter.get("buy_ma_compare_combo") or "").strip()
    operator = _direct_compare_operator(compare_text)
    if compare_text == "\ub3cc\ud30c":
        if direction == "\ud558\ud5a5":
            operator = "CROSS_DOWN"
        elif direction == "\uc0c1\ud5a5":
            operator = "CROSS_UP"
    if operator is None:
        warnings.append(f"buy MA compare is not mapped: {signal_filter.get('buy_ma_compare_combo')!r}")
        return None

    value = {
        "enabled": True,
        "conditions": [{
            "enabled": True,
            "not": False,
            "target": "CLOSE",
            "operator": operator,
            "compare_target": "MA",
            "period": period,
            "description": "UI preview: BUY current price / 60MA filter",
        }],
    }
    return {
        "path": BUY_MOVING_AVERAGE_FILTER_PATH,
        "value": value,
    }


def _moving_average_filter_value(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    return deepcopy(value) if isinstance(value, dict) else {}


def _price_compare_filter_value(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    return deepcopy(value) if isinstance(value, dict) else {}


def _bollinger_filter_value(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    return deepcopy(value) if isinstance(value, dict) else {}


def _ocr_filter_value(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    return deepcopy(value) if isinstance(value, dict) else {}


def _rsi_filter_value(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    return deepcopy(value) if isinstance(value, dict) else {}


def _composite_filter_value(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    return deepcopy(value) if isinstance(value, dict) else {}


def _execution_policy_value(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    return deepcopy(value) if isinstance(value, dict) else {}


def _execution_candidate_is_locked(candidate: dict[str, Any]) -> bool:
    return bool(candidate) and candidate.get("execution_connected") is False


def _method_policy_value(candidate: dict[str, Any]) -> Any:
    if "value" not in candidate:
        return _MISSING
    return deepcopy(candidate.get("value"))


def _set_path_value(root: dict[str, Any], path: str, value: Any) -> bool:
    parts = path.split(".")
    current: Any = root
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return False
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            return False
        current = child
    if not isinstance(current, dict):
        return False
    current[parts[-1]] = deepcopy(value)
    return True


def _set_buy_execution_policy_value(
    root: dict[str, Any],
    path: str,
    value: dict[str, Any],
) -> bool:
    if path not in _BUY_EXECUTION_PATHS:
        return False
    buy = root.get("buy")
    if not isinstance(buy, dict):
        return False
    execution = buy.get("execution")
    if execution is None:
        execution = {}
        buy["execution"] = execution
    if not isinstance(execution, dict):
        return False
    execution[path.rsplit(".", 1)[-1]] = deepcopy(value)
    return True


def _has_ui_value(values: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = values.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if value not in (None, ""):
            return True
    return False


def _choice_token(value: Any, mapping: dict[str, str], default: str | None = None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return default
    normalized = text.upper()
    if normalized in set(mapping.values()):
        return normalized
    return mapping.get(text, normalized)


def _price_basis_token(value: Any) -> str | None:
    return _choice_token(value, {
        "\uc8fc\ubb38\uac00": "ORDER_PRICE",
        "\ud604\uc7ac\uac00": "CURRENT_PRICE",
        "\uc885\uac00": "CLOSE",
        "\uc2dc\uc7a5\uac00": "MARKET",
        "\ud3c9\ub2e8\uac00": "AVG_PRICE",
    })


def _hoga_mode_token(value: Any) -> str | None:
    return _choice_token(value, {
        "\ub2e8\uc77c\ud638\uac00": "SINGLE",
        "\ub2e4\uc911\ud638\uac00": "MULTI",
    })


def _point_mode_token(value: Any) -> str | None:
    return _choice_token(value, {
        "\uc120\ud0dd\uc5c6\uc74c": "NONE",
        "\ub2e4\uc911\uc2dc\uac04": "MULTI_TIME",
        "\ub2e4\uc911\ube44\uc728": "MULTI_RATIO",
    }, "NONE")


def _point_unit_token(value: Any) -> str | None:
    return _choice_token(value, {
        "\ubd84": "MINUTE",
        "\ucd08": "SECOND",
        "\ubd09": "BAR",
    })


def _range_token(value: Any) -> str | None:
    return _choice_token(value, {
        "\uc774\ub0b4": "WITHIN",
        "\uac04\uaca9": "INTERVAL",
    })


def _direction_token(value: Any) -> str | None:
    return _choice_token(value, {
        "\uc0c1\ud5a5": "UP",
        "\ud558\ud5a5": "DOWN",
        "\uc0c1\ud558": "BOTH",
    })


def _ratio_compare_token(value: Any) -> str | None:
    operator = _compare_operator(value)
    if operator is not None:
        return operator
    return _choice_token(value, {
        "\uc774\ub0b4": "WITHIN",
        "\uc774\ud0c8": "OUTSIDE",
    })


def _detail_mode_token(value: Any) -> str | None:
    return _choice_token(value, {
        "\ud68c\ucc28\uae30\uc900": "ROUND",
        "\uc608\uc0b0\uae30\uc900": "BUDGET",
        "\ub2a5\ub3d9\ub9e4\uc218": "ACTIVE_BUY",
    })


def _round_operator_token(value: Any) -> str | None:
    return _choice_token(value, {
        "+": "ADD",
        "x": "MULTIPLY",
        "X": "MULTIPLY",
        "*": "MULTIPLY",
    })


def _strict_ui_bool(value: Any, *, default: bool | None = None) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None and default is not None:
        return default
    return None


def _nonnegative_float(value: Any) -> float | None:
    number = _safe_float(value)
    if number is None or not isfinite(number) or number < 0:
        return None
    return number


def _last_plus_one_method_token(value: Any) -> str | None:
    return _choice_token(value, {
        "시장가": "MARKET",
        "현재가": "CURRENT_PRICE",
        "능동": "ACTIVE",
    })


def _normalize_additional_mapper_state(
    additional: dict[str, Any],
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    price_defaults = {
        "check": False,
        "direction_combo": "상향",
        "ratio_line": "0.5",
        "compare_combo": "이하",
        "action": "SKIP_CURRENT_GENERATION",
    }
    last_defaults = {
        "check": False,
        "method_combo": "시장가",
        "direction_combo": "상향",
        "ratio_line": "0.45",
        "compare_combo": "이상",
    }
    if "price_compare_skip" in additional or "last_plus_one" in additional:
        price = deepcopy(price_defaults)
        last = deepcopy(last_defaults)
        if isinstance(additional.get("price_compare_skip"), dict):
            price.update(deepcopy(additional["price_compare_skip"]))
        if isinstance(additional.get("last_plus_one"), dict):
            last.update(deepcopy(additional["last_plus_one"]))
        price["action"] = "SKIP_CURRENT_GENERATION"
        return {"price_compare_skip": price, "last_plus_one": last}

    legacy_keys = {"check", "direction_combo", "ratio_line", "compare_combo", "method_combo"}
    price = deepcopy(price_defaults)
    last = deepcopy(last_defaults)
    if any(key in additional for key in legacy_keys):
        warnings.append("LEGACY_ADDITIONAL_STATE_PARTIAL_LOSS")
        for key in ("check", "direction_combo", "ratio_line", "compare_combo"):
            if key in additional:
                price[key] = deepcopy(additional[key])
        if "method_combo" in additional:
            last["method_combo"] = deepcopy(additional["method_combo"])
        last["check"] = False
    return {"price_compare_skip": price, "last_plus_one": last}


def _build_last_round_active_buy_policy(
    base: dict[str, Any],
    point_mode: str | None,
    warnings: list[str],
) -> tuple[dict[str, Any] | None, bool]:
    source = base.get("last_round_active_buy")
    if source is None:
        source = {}
    if not isinstance(source, dict):
        warnings.append("buy last-round active policy is not a dict")
        return None, False
    enabled = _strict_ui_bool(source.get("enabled"), default=False)
    if enabled is None:
        warnings.append("buy last-round active enabled must be boolean")
        return None, False
    policy = {
        "enabled": enabled,
        "applies_to": "LAST_MULTI_POINT_CHILD",
        "budget_policy_override": "NONE",
        "purpose": "BUY_METHOD_SPECIAL_ACTION",
        "subject": "AVERAGE_PRICE",
        "reference": "MULTI_POINT_SET_PRICE",
        "direction": _direction_token(source.get("direction", source.get("direction_combo", "상향"))),
        "ratio_percent": _nonnegative_float(source.get("ratio_percent", source.get("ratio_line", "0.45"))),
        "comparator": _ratio_compare_token(source.get("comparator", source.get("compare_combo", "이상"))),
    }
    if policy["direction"] not in {"UP", "DOWN", "BOTH"}:
        warnings.append("buy last-round active direction is invalid")
        return None, False
    if policy["ratio_percent"] is None:
        warnings.append("buy last-round active ratio is invalid")
        return None, False
    if policy["comparator"] not in {">=", "<=", "WITHIN", "OUTSIDE"}:
        warnings.append("buy last-round active comparator is invalid")
        return None, False
    if enabled and point_mode not in {"MULTI_TIME", "MULTI_RATIO"}:
        warnings.append("buy last-round active requires MULTI_TIME or MULTI_RATIO")
        return None, False
    return policy, False


def _build_buy_execution_additional_candidate(
    additional: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any] | None:
    if not additional:
        return None
    normalized = _normalize_additional_mapper_state(additional, warnings)
    price = normalized["price_compare_skip"]
    last = normalized["last_plus_one"]
    price_enabled = _strict_ui_bool(price.get("check"), default=False)
    last_enabled = _strict_ui_bool(last.get("check"), default=False)
    if price_enabled is None or last_enabled is None:
        warnings.append("buy additional enabled values must be boolean")
        return None

    price_policy = {
        "enabled": price_enabled,
        "reference_source": "PREVIOUS_CONFIRMED_BUY_ORDER_PRICE",
        "current_source": "ACTIONABLE_ORDER_PRICE",
        "direction": _direction_token(price.get("direction_combo")),
        "ratio_percent": _nonnegative_float(price.get("ratio_line")),
        "comparator": _ratio_compare_token(price.get("compare_combo")),
        "action": "SKIP_CURRENT_GENERATION",
        "skipped_round_increment": False,
    }
    if (
        price_policy["direction"] not in {"UP", "DOWN", "BOTH"}
        or price_policy["ratio_percent"] is None
        or price_policy["comparator"] not in {">=", "<=", "WITHIN", "OUTSIDE"}
    ):
        warnings.append("buy previous-round price skip policy is invalid")
        return None

    method = _last_plus_one_method_token(last.get("method_combo"))
    active_condition = {
        "lhs_source": "ACTIONABLE_ORDER_PRICE",
        "rhs_source": "AVERAGE_PRICE",
        "direction": _direction_token(last.get("direction_combo")),
        "ratio_percent": _nonnegative_float(last.get("ratio_line")),
        "comparator": _ratio_compare_token(last.get("compare_combo")),
    }
    if method not in {"MARKET", "CURRENT_PRICE", "ACTIVE"}:
        warnings.append("buy last+1 method is invalid")
        return None
    if (
        active_condition["direction"] not in {"UP", "DOWN", "BOTH"}
        or active_condition["ratio_percent"] is None
        or active_condition["comparator"] not in {">=", "<=", "WITHIN", "OUTSIDE"}
    ):
        warnings.append("buy last+1 active condition is invalid")
        return None
    last_policy = {
        "enabled": last_enabled,
        "generation_kind": "LAST_PLUS_ONE",
        "trigger": "AFTER_NORMAL_MAX_ROUND_COMPLETED",
        "max_occurrences": 1,
        "method": method,
        "active_condition": active_condition,
        "budget_basis": "LAST_NORMAL_ROUND_APPROVED_BUDGET",
        "terminal_after_completed_fill": True,
    }
    locked = False
    value = {
        "previous_round_price_skip": price_policy,
        "last_plus_one": last_policy,
        "execution_connected": not locked,
        "execution_lock_reason": P1_EXECUTION_LOCK_REASON if locked else "",
    }
    return {
        "path": BUY_EXECUTION_ADDITIONAL_PATH,
        "operation": "set_execution_policy",
        "value": value,
        "execution_connected": not locked,
        "execution_lock_reason": P1_EXECUTION_LOCK_REASON if locked else "",
    }


def _build_buy_execution_cycle_candidate(
    cycle: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any] | None:
    if not cycle or not any(str(key).startswith("buy_cycle_") for key in cycle):
        return None
    hoga_mode = _hoga_mode_token(cycle.get("buy_cycle_hoga_mode_combo"))
    point_mode = _point_mode_token(cycle.get("buy_cycle_time_mode_combo"))
    order_price_basis = _price_basis_token(cycle.get("buy_cycle_order_combo"))
    hoga_up = _safe_int(cycle.get("buy_cycle_hoga_up_line"))
    hoga_down = _safe_int(cycle.get("buy_cycle_hoga_down_line"))
    if hoga_mode not in {"SINGLE", "MULTI"} or point_mode not in {"NONE", "MULTI_TIME", "MULTI_RATIO"}:
        warnings.append("buy cycle mode is invalid")
        return None
    if hoga_mode == "SINGLE" and order_price_basis not in {"ORDER_PRICE", "CURRENT_PRICE", "MARKET"}:
        warnings.append("buy cycle order price basis is invalid")
        return None
    if hoga_mode == "MULTI" and (
        hoga_up is None or hoga_up < 0 or hoga_down is None or hoga_down < 0
    ):
        warnings.append("buy cycle multi-hoga range is invalid")
        return None

    point_policy: dict[str, Any] = {"mode": point_mode}
    if point_mode == "MULTI_TIME":
        point_policy.update({
            "value": _nonnegative_float(cycle.get("buy_cycle_time_value_line")),
            "unit": _point_unit_token(cycle.get("buy_cycle_time_unit_combo")),
            "range": _range_token(cycle.get("buy_cycle_time_range_combo")),
            "count": _safe_int(cycle.get("buy_cycle_time_count_line")),
            "order_price_basis": _price_basis_token(cycle.get("buy_cycle_time_order_combo")),
        })
        if (
            point_policy["value"] is None
            or point_policy["unit"] not in {"SECOND", "MINUTE", "BAR"}
            or point_policy["range"] not in {"WITHIN", "INTERVAL"}
            or not isinstance(point_policy["count"], int) or point_policy["count"] <= 0
            or point_policy["order_price_basis"] not in {"ORDER_PRICE", "CURRENT_PRICE"}
        ):
            warnings.append("buy cycle MULTI_TIME policy is invalid")
            return None
    elif point_mode == "MULTI_RATIO":
        point_policy.update({
            "left_source": _price_basis_token(cycle.get("buy_cycle_ratio_left_combo")),
            "right_source": _price_basis_token(cycle.get("buy_cycle_ratio_right_combo")),
            "direction": _direction_token(cycle.get("buy_cycle_ratio_direction_combo")),
            "ratio_percent": _nonnegative_float(cycle.get("buy_cycle_ratio_value_line")),
            "comparator": _ratio_compare_token(cycle.get("buy_cycle_ratio_compare_combo")),
            "count": _safe_int(cycle.get("buy_cycle_ratio_count_line")),
        })
        if (
            point_policy["left_source"] not in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
            or point_policy["right_source"] not in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
            or point_policy["direction"] not in {"UP", "DOWN", "BOTH"}
            or point_policy["ratio_percent"] is None
            or point_policy["comparator"] not in {">=", "<=", "WITHIN", "OUTSIDE"}
            or not isinstance(point_policy["count"], int) or point_policy["count"] <= 0
        ):
            warnings.append("buy cycle MULTI_RATIO policy is invalid")
            return None

    situation_mode = _choice_token(cycle.get("buy_cycle_situation_mode_combo"), {
        "미체결": "UNFILLED",
        "가격비교": "PRICE_COMPARE",
    })
    situation: dict[str, Any]
    if situation_mode == "UNFILLED":
        situation = {
            "mode": "UNFILLED",
            "action": "CANCEL",
            "scope": {"매회": "EACH", "일괄": "BATCH"}.get(cycle.get("buy_cycle_pending_scope_combo")),
            "configured_value": _nonnegative_float(cycle.get("buy_cycle_pending_value_line")),
            "configured_unit": _point_unit_token(cycle.get("buy_cycle_pending_unit_combo")),
            "anchor": "BROKER_ACCEPTED_AT",
        }
        if situation["scope"] not in {"EACH", "BATCH"} or situation["configured_value"] is None \
                or situation["configured_unit"] not in {"SECOND", "MINUTE", "BAR"}:
            warnings.append("buy cycle unfilled policy is invalid")
            return None
    elif situation_mode == "PRICE_COMPARE":
        situation = {
            "mode": "PRICE_COMPARE",
            "left_source": _price_basis_token(cycle.get("buy_cycle_price_left_combo")),
            "right_source": _price_basis_token(cycle.get("buy_cycle_price_right_combo")),
            "direction": _direction_token(cycle.get("buy_cycle_price_direction_combo")),
            "ratio_percent": _nonnegative_float(cycle.get("buy_cycle_price_value_line")),
            "comparator": _ratio_compare_token(cycle.get("buy_cycle_price_compare_combo")),
            "action": {"매수리셋": "RESET", "일괄취소": "CANCEL_BATCH"}.get(cycle.get("buy_cycle_price_action_combo")),
        }
        if (
            situation["left_source"] not in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
            or situation["right_source"] not in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
            or situation["direction"] not in {"UP", "DOWN", "BOTH"}
            or situation["ratio_percent"] is None
            or situation["comparator"] not in {">=", "<=", "WITHIN", "OUTSIDE"}
            or situation["action"] not in {"RESET", "CANCEL_BATCH"}
        ):
            warnings.append("buy cycle price response is invalid")
            return None
    else:
        warnings.append("buy cycle situation mode is invalid")
        return None

    connected = not (
        situation.get("mode") == "PRICE_COMPARE"
        and situation.get("action") == "CANCEL_BATCH"
    )
    lock_reason = "" if connected else CYCLE_OPTION_EXECUTION_LOCK_REASON
    value = {
        "scope": "SIGNAL_SCOPED_BUY_CYCLE",
        "requires_source_signal": True,
        "autonomous_scheduler": False,
        "after_cycle_completion": "REQUIRE_NEW_BUY_SIGNAL",
        "order_policy": {
            "hoga_mode": hoga_mode,
            "order_price_basis": order_price_basis,
            "hoga_up": hoga_up,
            "hoga_down": hoga_down,
        },
        "point_policy": point_policy,
        "situation_response": situation,
        "execution_connected": connected,
        "execution_lock_reason": lock_reason,
    }
    return {
        "path": BUY_EXECUTION_CYCLE_PATH,
        "operation": "set_execution_policy",
        "value": value,
        "execution_connected": connected,
        "execution_lock_reason": lock_reason,
    }


def _build_buy_execution_base_candidate(base: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    keys = (
        "hoga_combo",
        "order_combo",
        "up_line",
        "down_line",
        "time_mode_combo",
        "time_value_line",
        "time_unit_combo",
        "time_range_combo",
        "time_count_line",
        "time_order_combo",
        "ratio_left_combo",
        "ratio_right_combo",
        "ratio_direction_combo",
        "ratio_value_line",
        "ratio_compare_combo",
        "ratio_count_line",
    )
    if not _has_ui_value(base, keys):
        return None

    value = {
        "buy_phase": "BASE",
        "buy_round": 1,
        "budget_reference": "STARTING_BUDGET",
        "hoga_mode": _hoga_mode_token(base.get("hoga_combo")),
        "order_price_basis": _price_basis_token(base.get("order_combo")),
        "hoga_up": _safe_int(base.get("up_line")),
        "hoga_down": _safe_int(base.get("down_line")),
        "point_mode": _point_mode_token(base.get("time_mode_combo")),
        "point_value": _safe_float(base.get("time_value_line")),
        "point_unit": _point_unit_token(base.get("time_unit_combo")),
        "point_range": _range_token(base.get("time_range_combo")),
        "point_count": _safe_int(base.get("time_count_line")),
        "time_order_price_basis": _price_basis_token(base.get("time_order_combo")),
        "ratio_left": _price_basis_token(base.get("ratio_left_combo") or base.get("time_order_combo")),
        "ratio_right": _price_basis_token(base.get("ratio_right_combo")),
        "ratio_direction": _direction_token(base.get("ratio_direction_combo")),
        "ratio_value": _safe_float(base.get("ratio_value_line")),
        "ratio_compare": _ratio_compare_token(base.get("ratio_compare_combo")),
        "ratio_count": _safe_int(base.get("ratio_count_line")),
    }
    last_round_active_buy, active_execution_lock = _build_last_round_active_buy_policy(
        base,
        value["point_mode"],
        warnings,
    )
    if last_round_active_buy is None:
        return None
    value["last_round_active_buy"] = last_round_active_buy
    value["execution_connected"] = not active_execution_lock
    value["execution_lock_reason"] = (
        P1_EXECUTION_LOCK_REASON if active_execution_lock else ""
    )
    return {
        "path": BUY_EXECUTION_BASE_PATH,
        "operation": "set_execution_policy",
        "value": value,
        "execution_connected": not active_execution_lock,
        "execution_lock_reason": (
            P1_EXECUTION_LOCK_REASON if active_execution_lock else ""
        ),
    }


def _build_buy_exit_policy(exit_state: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    """Normalize the BUY repeat-exit controls into the execution rule.

    The controls are optional; only checked rows become executable policy
    conditions.  The policy is deliberately attached to the existing BUY
    execution base so no new writer or runtime state is introduced.
    """
    if not isinstance(exit_state, dict) or not exit_state:
        return None
    conditions: list[dict[str, Any]] = []
    if _truthy_ui(exit_state.get("buy_exit_count_check")):
        value = _safe_int(exit_state.get("buy_exit_count_line"))
        if value is None or value <= 0:
            warnings.append("buy exit count is not numeric")
        else:
            conditions.append({
                "condition_type": "COUNT",
                "target_repeat_generations": value,
                "initial_generation_included": False,
            })
    if _truthy_ui(exit_state.get("buy_exit_time_check")):
        value = _safe_float(exit_state.get("buy_exit_time_line"))
        unit = _point_unit_token(exit_state.get("buy_exit_time_unit_combo"))
        unit_ms = {"SECOND": 1000, "MINUTE": 60_000, "BAR": None}.get(unit)
        if value is None or value <= 0 or unit is None or (unit != "BAR" and unit_ms is None):
            warnings.append("buy exit time is invalid")
        else:
            condition: dict[str, Any] = {
                "condition_type": "TIME",
                "configured_value": value,
                "configured_unit": unit,
                "anchor": "FIRST_REPEAT_GENERATION_AT",
            }
            if unit_ms is not None:
                duration = value * unit_ms
                if float(duration).is_integer():
                    condition["duration_ms"] = int(duration)
                else:
                    warnings.append("buy exit time duration is invalid")
                    condition = {}
            if condition:
                conditions.append(condition)
    if _truthy_ui(exit_state.get("buy_exit_price_check")):
        left = _price_basis_token(exit_state.get("buy_exit_price_left_combo"))
        right = _price_basis_token(exit_state.get("buy_exit_price_right_combo"))
        direction = _direction_token(exit_state.get("buy_exit_price_direction_combo"))
        compare = _ratio_compare_token(exit_state.get("buy_exit_price_compare_combo"))
        threshold = _safe_float(exit_state.get("buy_exit_price_value_line"))
        if left not in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"} \
                or right not in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"} \
                or direction not in {"UP", "DOWN", "BOTH"} \
                or compare not in {"WITHIN", "OUTSIDE", ">=", "<=", "==", "!=", ">", "<"} \
                or threshold is None or threshold <= 0:
            warnings.append("buy exit price policy is invalid")
        else:
            conditions.append({
                "condition_type": "PRICE",
                "left_source": left,
                "right_source": right,
                "direction": direction,
                "compare": compare,
                "threshold_percent": threshold,
                "orientation": "LEFT_VALUE_RELATIVE_TO_RIGHT_BASE",
            })
    if not conditions:
        return None
    policy = {
        "policy": "BUY_REPEAT_EXIT",
        "enabled": True,
        "logic": "OR",
        "conditions": conditions,
        "completion_behavior": "BLOCK_FUTURE_BUY_ROUNDS",
    }
    policy["snapshot_hash"] = _stable_hash(policy)
    return policy


def _build_buy_execution_repeat_candidate(
    repeat: dict[str, Any],
    warnings: list[str],
    *,
    legacy_base: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not _truthy_ui(repeat.get("apply_all_check")):
        return None

    detail_mode_value = repeat.get("detail_mode_combo")
    if detail_mode_value in (None, ""):
        detail_mode_value = _as_dict(legacy_base).get("detail_mode_combo")
    value = {
        "buy_phase": "REPEAT",
        "starts_from_round": 2,
        "apply_all": True,
        "detail_mode": _detail_mode_token(detail_mode_value),
        "round_operator": _round_operator_token(repeat.get("round_operator_combo")),
        "round_budget_value": _safe_float(repeat.get("round_budget_line")),
        "budget_ratio": _safe_float(repeat.get("budget_ratio_line")),
        "active_direction": _direction_token(repeat.get("active_direction_combo")),
        "active_ratio": _safe_float(repeat.get("active_ratio_line")),
        "active_compare": _ratio_compare_token(repeat.get("active_compare_combo")),
    }
    return {
        "path": BUY_EXECUTION_REPEAT_PATH,
        "operation": "set_execution_policy",
        "value": value,
    }


def _selected_sell_method_sets(value: Any) -> list[str] | None:
    if not isinstance(value, dict):
        return None
    selected: list[str] = []
    for ui_key, rule_key in (("a", "setting_a"), ("b", "setting_b"), ("c", "setting_c")):
        if _truthy_ui(value.get(ui_key)):
            selected.append(rule_key)
    return selected


def _build_sell_method_policy_candidates(
    sell_ui: dict[str, Any],
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    raw_selected_sets = sell_ui.get("selected_sets")
    selected_sets = _selected_sell_method_sets(raw_selected_sets)
    if selected_sets is not None:
        candidates[SELL_METHOD_SELECTED_SETS_PATH] = {
            "path": SELL_METHOD_SELECTED_SETS_PATH,
            "operation": "set_method_policy",
            "candidate_type": "set_method_policy",
            "value": selected_sets,
            "preview_only": True,
            "execution_connected": False,
            "runtime_write": False,
            "send_order": False,
        }
        if len(selected_sets) != 1:
            warnings.append("sell method selected_sets must select exactly one of setting_a/setting_b/setting_c")
    elif raw_selected_sets is not None:
        warnings.append("sell method selected_sets must select exactly one of setting_a/setting_b/setting_c")

    for ui_key, path in _SELL_METHOD_SETTING_PATHS.items():
        setting = sell_ui.get(ui_key)
        if not isinstance(setting, dict) or not setting:
            continue
        value = deepcopy(setting)
        value["preview_only"] = False
        value["execution_connected"] = True
        value["runtime_write"] = False
        value["send_order"] = False
        candidates[path] = {
            "path": path,
            "operation": "set_method_policy",
            "candidate_type": "set_method_policy",
            "value": value,
        }
    return candidates


def _build_buy_bollinger_filter_candidate(signal_filter: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if not _optional_filter_enabled(signal_filter, "buy_bollinger_enabled", "buy_bollinger_value_line"):
        return None

    threshold = _safe_float(signal_filter.get("buy_bollinger_value_line"))
    operator = _compare_operator(signal_filter.get("buy_bollinger_compare_combo"))
    if threshold is None:
        warnings.append("buy Bollinger threshold is not numeric")
        return None
    if operator is None:
        warnings.append(f"buy Bollinger compare is not mapped: {signal_filter.get('buy_bollinger_compare_combo')!r}")
        return None

    direction = str(signal_filter.get("buy_bollinger_direction_combo") or "").strip()
    signed_threshold = -abs(threshold) if direction == "\ud558\ud5a5" else abs(threshold)
    return {
        "path": BUY_BOLLINGER_FILTER_PATH,
        "value": {
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "not": False,
                "target": "CLOSE",
                "operator": operator,
                "compare_target": "BOLLINGER",
                "value": signed_threshold,
                "description": "UI preview: BUY current price / Bollinger filter",
            }],
        },
    }


def _price_compare_operator(value: Any) -> str | None:
    text = str(value or "").strip()
    if text == "=<":
        return "<="
    return _compare_operator(text)


def _price_compare_condition(
    *,
    target: str,
    operator: str,
    compare_target: str,
    value: float | None = None,
    description: str,
) -> dict[str, Any]:
    condition: dict[str, Any] = {
        "enabled": True,
        "not": False,
        "target": target,
        "operator": operator,
        "compare_target": compare_target,
        "description": description,
    }
    if value is not None:
        condition["value"] = value
    return condition


def _build_buy_price_compare_filter_candidate(price_compare: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if "check" in price_compare:
        if not _truthy_ui(price_compare.get("check")):
            return None
    elif not _optional_filter_enabled(price_compare, "enabled", "ratio_line"):
        return None

    type_text = str(price_compare.get("type_combo") or "").strip()
    if type_text and type_text != "\uac00\uaca9\ube44\uad50":
        return None

    target = _series_target(price_compare.get("left_combo"))
    compare_target = _series_target(price_compare.get("right_combo"))
    threshold = _safe_float(price_compare.get("ratio_line"))
    operator = _price_compare_operator(price_compare.get("compare_combo"))
    if any(key in price_compare for key in ("left_combo", "right_combo", "ratio_line", "compare_combo")):
        if target is None:
            warnings.append(f"buy price compare left target is not mapped: {price_compare.get('left_combo')!r}")
            return None
        if compare_target is None:
            warnings.append(f"buy price compare right target is not mapped: {price_compare.get('right_combo')!r}")
            return None
        if threshold is None:
            warnings.append("buy price compare ratio is not numeric")
            return None
        if operator is None:
            warnings.append(f"buy price compare operator is not mapped: {price_compare.get('compare_combo')!r}")
            return None

        conditions = [_price_compare_condition(
            target=target,
            operator=operator,
            compare_target=compare_target,
            value=threshold,
            description="UI preview: BUY price compare filter condition",
        )]
    else:
        below_operator = _price_compare_operator(price_compare.get("condition_combo"))
        above_operator = _price_compare_operator(price_compare.get("above_condition_combo"))
        conditions: list[dict[str, Any]] = []
        if below_operator not in {"<", "<="}:
            warnings.append(f"buy price compare condition is not mapped: {price_compare.get('condition_combo')!r}")
            return None
        if above_operator not in {">", ">="}:
            warnings.append(f"buy price compare above condition is not mapped: {price_compare.get('above_condition_combo')!r}")
            return None
        # The two simultaneous Production branches have one exact, gap-free boundary.
        below_operator = "<="
        above_operator = ">"
        has_branch_policy_fields = any(
            key in price_compare
            for key in (
                "round_operator_combo",
                "round_budget_line",
                "budget_ratio_line",
                "above_round_operator_combo",
                "above_round_budget_line",
                "above_budget_ratio_line",
            )
        )
        below_policy = _buy_price_compare_branch_policy(price_compare, "", warnings) if has_branch_policy_fields else None
        above_policy = _buy_price_compare_branch_policy(price_compare, "above_", warnings) if has_branch_policy_fields else None
        if has_branch_policy_fields and (below_policy is None or above_policy is None):
            return None
        below_condition = _price_compare_condition(
            target="AVG_PRICE",
            operator=below_operator,
            compare_target="ORDER_PRICE",
            description="UI preview: BUY price compare below-branch filter condition",
        )
        if below_policy is not None:
            below_condition.update({"branch_id": "BELOW_OR_EQUAL", "branch_policy": below_policy})
        conditions.append(below_condition)
        above_condition = _price_compare_condition(
            target="AVG_PRICE",
            operator=above_operator,
            compare_target="ORDER_PRICE",
            description="UI preview: BUY price compare above-branch filter condition",
        )
        if above_policy is not None:
            above_condition.update({"branch_id": "ABOVE", "branch_policy": above_policy})
        conditions.append(above_condition)

    return {
        "path": BUY_PRICE_COMPARE_FILTER_PATH,
        "value": {
            "enabled": True,
            "conditions_logic": "OR",
            "conditions": conditions,
        },
    }


def _buy_price_compare_branch_policy(
    price_compare: dict[str, Any],
    prefix: str,
    warnings: list[str],
) -> dict[str, Any] | None:
    label = "above" if prefix else "below"
    mode = str(price_compare.get(f"{prefix}mode_combo") or "").strip()
    if mode == "회차기준":
        operator = str(price_compare.get(f"{prefix}round_operator_combo") or "").strip()
        operator_token = {"+": "ADD", "x": "MULTIPLY", "X": "MULTIPLY"}.get(operator)
        value = _safe_float(price_compare.get(f"{prefix}round_budget_line"))
        if operator_token is None or value is None or value <= 0:
            warnings.append(f"buy price compare {label} round policy is invalid")
            return None
        return {
            "detail_mode": "ROUND",
            "round_operator": operator_token,
            "round_budget_value": value,
        }
    if mode == "예산기준":
        ratio = _safe_float(price_compare.get(f"{prefix}budget_ratio_line"))
        if ratio is None or ratio <= 0:
            warnings.append(f"buy price compare {label} budget policy is invalid")
            return None
        return {"detail_mode": "BUDGET", "budget_ratio": ratio}
    if mode == "능동매수":
        warnings.append("buy price compare ACTIVE_BUY remains reserved")
        return None
    warnings.append(f"buy price compare {label} mode is not mapped: {mode!r}")
    return None


def _build_sell_condition_c_indicator_condition(condition_c: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if condition_c.get("macd_check") is False:
        return None

    target = {
        "MACD\uc120": "MACD",
        "\uc2dc\uadf8\ub110\uc120": "SIGNAL",
    }.get(str(condition_c.get("macd_kind_combo", "")).strip())
    operator = _compare_operator(condition_c.get("macd_compare_combo"))
    value = _signed_float(
        condition_c.get("macd_sign_combo"),
        condition_c.get("macd_value_line"),
    )

    if target is None:
        warnings.append(f"sell condition C MACD target is not mapped: {condition_c.get('macd_kind_combo')!r}")
        return None
    if operator is None:
        warnings.append(f"sell condition C MACD compare is not mapped: {condition_c.get('macd_compare_combo')!r}")
        return None
    if value is None:
        warnings.append("sell condition C MACD value is not numeric")
        return None

    return {
        "enabled": True,
        "not": False,
        "target": target,
        "operator": operator,
        "value": value,
        "description": "UI preview: sell condition C MACD line threshold",
    }


def _build_sell_condition_c_array_conditions(condition_c: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]] | None:
    if condition_c.get("array_check") is False:
        return []

    if not any(key in condition_c for key in (
        "array_first_period_combo",
        "array_first_compare_combo",
        "array_second_period_combo",
        "array_second_compare_combo",
        "array_third_period_combo",
    )):
        return []

    first_period = _safe_int(condition_c.get("array_first_period_combo"))
    second_period = _safe_int(condition_c.get("array_second_period_combo"))
    third_period = _safe_int(condition_c.get("array_third_period_combo"))
    first_operator = _direct_compare_operator(condition_c.get("array_first_compare_combo"))
    second_operator = _direct_compare_operator(condition_c.get("array_second_compare_combo"))

    if first_period is None or first_period <= 0:
        warnings.append("sell condition C ARRAY first period is not numeric")
        return None
    if second_period is None or second_period <= 0:
        warnings.append("sell condition C ARRAY second period is not numeric")
        return None
    if third_period is None or third_period <= 0:
        warnings.append("sell condition C ARRAY third period is not numeric")
        return None
    if first_operator not in {">", ">=", "<", "<="}:
        warnings.append(f"sell condition C ARRAY first compare is not mapped: {condition_c.get('array_first_compare_combo')!r}")
        return None
    if second_operator not in {">", ">=", "<", "<="}:
        warnings.append(f"sell condition C ARRAY second compare is not mapped: {condition_c.get('array_second_compare_combo')!r}")
        return None

    return [
        {
            "enabled": True,
            "not": False,
            "target": "MA",
            "period": first_period,
            "operator": first_operator,
            "compare_target": "MA",
            "compare_period": second_period,
            "description": "UI preview: sell condition C ARRAY first MA comparison",
        },
        {
            "enabled": True,
            "not": False,
            "target": "MA",
            "period": second_period,
            "operator": second_operator,
            "compare_target": "MA",
            "compare_period": third_period,
            "description": "UI preview: sell condition C ARRAY second MA comparison",
        },
    ]


def _build_sell_gap_condition(source: dict[str, Any], warnings: list[str], label: str) -> dict[str, Any] | None:
    if source.get("gap_check") is False:
        return None
    if not any(key in source for key in ("gap_left_combo", "gap_right_combo", "gap_direction_combo", "gap_value_line", "gap_compare_combo")):
        return None
    left = _series_target(source.get("gap_left_combo"))
    right = _series_target(source.get("gap_right_combo"))
    direction = {"상향": "UP", "하향": "DOWN", "상하": "BOTH", "UP": "UP", "DOWN": "DOWN", "BOTH": "BOTH"}.get(str(source.get("gap_direction_combo") or "").strip())
    compare_mode = {"이상": "GTE", "이하": "LTE", "이내": "WITHIN", "이탈": "OUTSIDE", "GTE": "GTE", "LTE": "LTE", "WITHIN": "WITHIN", "OUTSIDE": "OUTSIDE"}.get(str(source.get("gap_compare_combo") or "").strip())
    value = _safe_float(source.get("gap_value_line"))
    if left is None or right is None or direction is None or compare_mode is None or value is None or value < 0:
        warnings.append(f"{label} GAP policy is invalid")
        return None
    if direction == "BOTH" and compare_mode not in {"WITHIN", "OUTSIDE"}:
        warnings.append(f"{label} GAP BOTH requires WITHIN/OUTSIDE")
        return None
    if direction != "BOTH" and compare_mode not in {"GTE", "LTE"}:
        warnings.append(f"{label} GAP directional policy requires GTE/LTE")
        return None
    return {
        "enabled": True,
        "not": False,
        "target": left,
        "operator": "PERCENT_GAP",
        "compare_target": right,
        "direction": direction,
        "compare_mode": compare_mode,
        "value": value,
        "description": f"UI preview: {label} GAP condition",
    }


def _sell_group_from_rows(
    *,
    name: str,
    rows: list[tuple[str, list[dict[str, Any]], Any]],
    warnings: list[str],
) -> dict[str, Any] | None:
    active: list[tuple[list[str], Any]] = []
    conditions: list[dict[str, Any]] = []
    for row_name, row_conditions, operator_after in rows:
        if not row_conditions:
            continue
        ids: list[str] = []
        for index, condition in enumerate(row_conditions):
            expression_id = f"{row_name}_{index}"
            condition["expression_id"] = expression_id
            ids.append(expression_id)
            conditions.append(condition)
        active.append((ids, operator_after))
    if not active:
        return None
    if len(active) == 1:
        for condition in conditions:
            condition.pop("expression_id", None)
        return {"enabled": True, "name": name, "conditions_logic": "AND", "conditions": conditions}
    expression = _fold_expression_ids(active[0][0])
    for index in range(1, len(active)):
        right = _fold_expression_ids(active[index][0])
        operator = str(active[index - 1][1] or "AND").strip().upper()
        if operator not in {"AND", "OR", "NOT"} or expression is None or right is None:
            warnings.append(f"{name} row logic is not supported: {active[index - 1][1]!r}")
            return None
        expression = {"type": "binary", "operator": operator, "left": expression, "right": right}
    group = {"enabled": True, "name": name, "conditions_logic": "AND", "conditions": conditions}
    if expression is not None:
        group["condition_expression"] = expression
    return group


def _build_sell_condition_c_signal_candidate(condition_c: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    macd_condition = _build_sell_condition_c_indicator_condition(condition_c, warnings)
    gap_condition = _build_sell_gap_condition(condition_c, warnings, "sell condition C")
    array_conditions = _build_sell_condition_c_array_conditions(condition_c, warnings)
    if array_conditions is None:
        return None
    group = _sell_group_from_rows(
        name="UI_PREVIEW_SELL_CONDITION_C",
        rows=[
            ("GAP", [gap_condition] if gap_condition else [], condition_c.get("gap_logic_combo")),
            ("MACD", [macd_condition] if macd_condition else [], condition_c.get("macd_logic_combo")),
            ("ARRAY", array_conditions, None),
        ],
        warnings=warnings,
    )
    if group is None:
        return None

    return {
        "path": SELL_CONDITION_C_SIGNAL_PREVIEW_PATH,
        "candidate_type": "add_signal",
        "value": {
            "enabled": True,
            "preview_candidate": True,
            "groups_logic": "OR",
            "groups": [group],
        },
    }


def _build_sell_condition_a_ocr_conditions(condition_a: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]] | None:
    if condition_a.get("ocr_check") is False:
        return []

    if not any(key in condition_a for key in (
        "ocr_direction_combo",
        "ocr_turn_combo",
        "ocr_convert_line",
        "ocr_compare_combo",
        "ocr_sign_combo",
        "ocr_value_line",
    )):
        return []

    conditions: list[dict[str, Any]] = []
    turn_text = str(condition_a.get("ocr_direction_combo", condition_a.get("ocr_turn_combo", ""))).strip()
    raw_bar_offset = condition_a.get("ocr_convert_line", 0)
    bar_offset = _safe_int(raw_bar_offset)
    if bar_offset is None or bar_offset < 0:
        warnings.append("sell condition A OCR convert bar is not a non-negative integer")
        return None
    if turn_text:
        turn_operator = {
            "\uc0c1\uc2b9": "TURN_UP",
            "\ud558\ub77d": "TURN_DOWN",
            "TURN_UP": "TURN_UP",
            "TURN_DOWN": "TURN_DOWN",
        }.get(turn_text)
        if turn_operator is None:
            warnings.append(f"sell condition A OCR turn is not mapped: {turn_text!r}")
            return None
        conditions.append({
            "enabled": True,
            "not": False,
            "target": "OSC",
            "operator": turn_operator,
            "description": "UI preview: sell condition A OCR/OSC turn condition",
        })
        if "ocr_convert_line" in condition_a:
            conditions[-1]["bar_offset"] = bar_offset

    raw_threshold = condition_a.get("ocr_value_line")
    if raw_threshold not in (None, ""):
        compare_operator = _compare_operator(condition_a.get("ocr_compare_combo"))
        threshold = _signed_float(condition_a.get("ocr_sign_combo"), raw_threshold)
        if compare_operator is None:
            warnings.append(f"sell condition A OCR compare is not mapped: {condition_a.get('ocr_compare_combo')!r}")
            return None
        if threshold is None:
            warnings.append("sell condition A OCR threshold is not numeric")
            return None
        conditions.append({
            "enabled": True,
            "not": False,
            "target": "OSC",
            "operator": compare_operator,
            "value": threshold,
            "description": "UI preview: sell condition A OCR/OSC threshold condition",
        })
        if "ocr_convert_line" in condition_a:
            conditions[-1]["bar_offset"] = bar_offset

    return conditions


def _build_sell_condition_a_rsi_condition(condition_a: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if condition_a.get("rsi_check") is False:
        return None

    if not any(key in condition_a for key in (
        "rsi_period_line",
        "rsi_compare_combo",
        "rsi_value_line",
    )):
        return None

    raw_period = condition_a.get("rsi_period_line")
    raw_threshold = condition_a.get("rsi_value_line")
    if raw_period in (None, "") or raw_threshold in (None, ""):
        warnings.append("sell condition A RSI period or threshold is missing")
        return None

    period = _safe_int(raw_period)
    operator = _compare_operator(condition_a.get("rsi_compare_combo"))
    threshold = _safe_float(raw_threshold)
    if period is None or period <= 0:
        warnings.append("sell condition A RSI period is not numeric")
        return None
    if operator is None:
        warnings.append(f"sell condition A RSI compare is not mapped: {condition_a.get('rsi_compare_combo')!r}")
        return None
    if threshold is None:
        warnings.append("sell condition A RSI threshold is not numeric")
        return None

    return {
        "enabled": True,
        "not": False,
        "target": "RSI",
        "period": period,
        "operator": operator,
        "value": threshold,
        "description": "UI preview: sell condition A RSI threshold condition",
    }


def _build_sell_condition_a_signal_candidate(condition_a: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    ocr_conditions = _build_sell_condition_a_ocr_conditions(condition_a, warnings)
    if ocr_conditions is None:
        return None
    gap_condition = _build_sell_gap_condition(condition_a, warnings, "sell condition A")
    rsi_condition = _build_sell_condition_a_rsi_condition(condition_a, warnings)
    group = _sell_group_from_rows(
        name="condition_a",
        rows=[
            ("OCR", ocr_conditions, condition_a.get("ocr_logic_combo")),
            ("GAP", [gap_condition] if gap_condition else [], condition_a.get("gap_logic_combo")),
            ("RSI", [rsi_condition] if rsi_condition else [], None),
        ],
        warnings=warnings,
    )
    if group is None:
        return None

    return {
        "path": SELL_CONDITION_A_SIGNAL_PREVIEW_PATH,
        "candidate_type": "add_signal",
        "value": {
            "enabled": True,
            "preview_candidate": True,
            "groups_logic": "OR",
            "groups": [group],
        },
    }


def _expression_identifier(name: str) -> dict[str, Any]:
    return {"type": "identifier", "name": name}


def _fold_expression_ids(identifiers: list[str]) -> dict[str, Any] | None:
    if not identifiers:
        return None
    node = _expression_identifier(identifiers[0])
    for identifier in identifiers[1:]:
        node = {
            "type": "binary",
            "operator": "AND",
            "left": node,
            "right": _expression_identifier(identifier),
        }
    return node


def _row_logic_expression(
    left_ids: list[str],
    right_ids: list[str],
    operator_value: Any,
    warnings: list[str],
    label: str,
) -> dict[str, Any] | None:
    left = _fold_expression_ids(left_ids)
    right = _fold_expression_ids(right_ids)
    if left is None:
        return right
    if right is None:
        return left
    operator = str(operator_value or "AND").strip().upper()
    if operator not in {"AND", "OR", "NOT"}:
        warnings.append(f"{label} logic is not supported: {operator_value!r}")
        return None
    return {"type": "binary", "operator": operator, "left": left, "right": right}


def _attach_sell_signal_expression(
    candidates: dict[str, dict[str, Any]],
    expression: Any,
    warnings: list[str],
) -> None:
    expression_text = str(expression or "").strip()
    if not expression_text or not candidates:
        return
    parsed = parse_condition_expression(
        expression_text,
        allowed_identifiers={"A", "B", "C"},
        allow_duplicate_identifiers=False,
    )
    if not parsed.get("ok"):
        warnings.append(f"sell signal expression is invalid: {parsed.get('reason')}")
        candidates.clear()
        return
    expression_value = {
        "source": expression_text,
        "normalized": parsed["normalized"],
        "ast": parsed["ast"],
        "identifiers": parsed["identifiers"],
        "identifier_map": {
            "A": APPROVED_SELL_CONDITION_A_SIGNAL_KEY,
            "B": APPROVED_SELL_CONDITION_B_SIGNAL_KEY,
            "C": APPROVED_SELL_CONDITION_C_SIGNAL_KEY,
        },
    }
    for candidate in candidates.values():
        value = _as_dict(candidate.get("value"))
        value["signal_expression"] = deepcopy(expression_value)


def _build_sell_condition_b_price_box_condition(condition_b: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if condition_b.get("price_box_check") is False:
        return None
    if not any(key in condition_b for key in ("price_box_direction_combo", "price_box_value_line", "price_box_compare_combo")):
        return None
    compare_target = {
        "상향": "PRICE_BOX_UPPER",
        "하향": "PRICE_BOX_LOWER",
        "UPPER": "PRICE_BOX_UPPER",
        "LOWER": "PRICE_BOX_LOWER",
    }.get(str(condition_b.get("price_box_direction_combo") or "").strip())
    operator = _compare_operator(condition_b.get("price_box_compare_combo"))
    value = _safe_float(condition_b.get("price_box_value_line"))
    if compare_target is None or operator not in {">=", "<="} or value is None or value < 0:
        warnings.append("sell condition B Price Box policy is invalid")
        return None
    return {
        "enabled": True,
        "not": False,
        "target": "CLOSE",
        "operator": operator,
        "compare_target": compare_target,
        "value": value,
        "description": "UI preview: sell condition B Price Box condition",
    }


def _build_sell_condition_b_bollinger_condition(condition_b: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if condition_b.get("bollinger_check") is False:
        return None

    if not any(key in condition_b for key in (
        "bollinger_direction_combo",
        "bollinger_value_line",
        "bollinger_compare_combo",
    )):
        return None

    direction = str(condition_b.get("bollinger_direction_combo") or "").strip()
    compare_target = {
        "\uc0c1\ud5a5": "BOLLINGER_UPPER",
        "\ud558\ud5a5": "BOLLINGER_LOWER",
        "UPPER": "BOLLINGER_UPPER",
        "LOWER": "BOLLINGER_LOWER",
    }.get(direction)
    if compare_target is None:
        warnings.append(f"sell condition B Bollinger direction is not mapped: {condition_b.get('bollinger_direction_combo')!r}")
        return None

    operator = _compare_operator(condition_b.get("bollinger_compare_combo"))
    if operator is None:
        warnings.append(f"sell condition B Bollinger compare is not mapped: {condition_b.get('bollinger_compare_combo')!r}")
        return None

    offset = _safe_float(condition_b.get("bollinger_value_line"))
    if offset is None:
        warnings.append("sell condition B Bollinger offset is not numeric")
        return None
    return {
        "enabled": True,
        "not": False,
        "target": "CLOSE",
        "operator": operator,
        "compare_target": compare_target,
        "value": abs(offset) if compare_target == "BOLLINGER_UPPER" else -abs(offset),
        "description": "UI preview: sell condition B Bollinger band condition",
    }


def _build_sell_condition_b_signal_candidate(condition_b: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    price_box_condition = _build_sell_condition_b_price_box_condition(condition_b, warnings)
    bollinger_condition = _build_sell_condition_b_bollinger_condition(condition_b, warnings)
    gap_condition = _build_sell_gap_condition(condition_b, warnings, "sell condition B")
    group = _sell_group_from_rows(
        name="condition_b",
        rows=[
            ("PRICE_BOX", [price_box_condition] if price_box_condition else [], condition_b.get("price_box_logic_combo")),
            ("BOLLINGER", [bollinger_condition] if bollinger_condition else [], condition_b.get("bollinger_logic_combo")),
            ("GAP", [gap_condition] if gap_condition else [], None),
        ],
        warnings=warnings,
    )
    if group is None:
        return None

    return {
        "path": SELL_CONDITION_B_SIGNAL_PREVIEW_PATH,
        "candidate_type": "add_signal",
        "value": {
            "enabled": True,
            "preview_candidate": True,
            "groups_logic": "OR",
            "groups": [group],
        },
    }


def _sell_add_signal_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    if isinstance(value, dict):
        return deepcopy(value)
    return deepcopy(candidate)


def _sell_add_signal_target(source_path: str) -> tuple[str, str] | None:
    return _SELL_ADD_SIGNAL_TARGETS.get(source_path)


def _sell_add_signal_candidate(preview_candidates: dict[str, Any], source_path: str) -> dict[str, Any]:
    sell_candidates = _as_dict(preview_candidates.get("sell"))
    candidates_by_path = _as_dict(sell_candidates.get("add_signal_candidates"))
    candidate = _as_dict(candidates_by_path.get(source_path))
    if candidate:
        return candidate
    legacy_candidate = _as_dict(sell_candidates.get("add_signal_candidate"))
    if str(legacy_candidate.get("path") or "") == source_path:
        return legacy_candidate
    return {}


def _sell_set_signal_candidate(preview_candidates: dict[str, Any], source_path: str) -> dict[str, Any]:
    sell_candidates = _as_dict(preview_candidates.get("sell"))
    candidates_by_path = _as_dict(sell_candidates.get("set_signal_candidates"))
    return _as_dict(candidates_by_path.get(source_path))


def _profit_rate_signal_value(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    if not isinstance(value, dict):
        return {}
    return {
        key: deepcopy(value[key])
        for key in ("enabled", "profit_rate_percent", "basis")
        if key in value
    }


def _build_sell_profit_rate_signal_candidate(
    sell_ui: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any] | None:
    profit_rate_sell = _as_dict(sell_ui.get("profit_rate_sell"))
    if not profit_rate_sell:
        return None

    basis = str(profit_rate_sell.get("basis") or "").strip()
    if basis != "average_price":
        warnings.append(f"sell profit_rate_sell basis is not supported: {profit_rate_sell.get('basis')!r}")
        return None

    profit_rate_percent = _safe_float(profit_rate_sell.get("profit_rate_percent"))
    if profit_rate_percent is None:
        warnings.append("sell profit_rate_sell profit_rate_percent is not numeric")
        return None
    if profit_rate_percent < 0:
        warnings.append("sell profit_rate_sell negative profit_rate_percent is not supported")
        return None

    return {
        "path": SELL_PROFIT_RATE_SIGNAL_PATH,
        "candidate_type": "set_signal",
        "value": {
            "enabled": bool(profit_rate_sell.get("enabled", False)),
            "profit_rate_percent": profit_rate_percent,
            "basis": basis,
        },
    }


def _condition_matches(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if existing.get("target") != candidate.get("target"):
        return False
    if existing.get("operator") != candidate.get("operator"):
        return False
    if existing.get("compare_target") != candidate.get("compare_target"):
        return False
    if existing.get("period") != candidate.get("period"):
        return False
    if "value" in candidate and existing.get("value") != candidate.get("value"):
        return False
    return True


def _build_buy_merge_candidate(
    current_rules: dict[str, Any],
    buy_conditions: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any] | None:
    buy_section = _as_dict(current_rules.get("buy"))
    groups = buy_section.get("groups")
    if not isinstance(groups, list) or not groups or not isinstance(groups[0], dict):
        warnings.append("current buy.groups[0] is not available; buy merge candidate was not generated")
        return None

    existing_conditions = groups[0].get("conditions")
    if not isinstance(existing_conditions, list):
        warnings.append("current buy.groups[0].conditions is not a list; buy merge candidate was not generated")
        return None

    skip_existing: list[dict[str, Any]] = []
    add_conditions: list[dict[str, Any]] = []
    for condition in buy_conditions:
        if any(isinstance(existing, dict) and _condition_matches(existing, condition) for existing in existing_conditions):
            skipped = {
                "target": condition.get("target"),
                "operator": condition.get("operator"),
                "reason": "already exists in current buy.groups[0]",
            }
            if condition.get("compare_target") is not None:
                skipped["compare_target"] = condition.get("compare_target")
            if condition.get("period") is not None:
                skipped["period"] = condition.get("period")
            skip_existing.append(skipped)
        else:
            add_conditions.append(condition)

    return {
        "merge_into": BUY_CONDITIONS_PATH,
        "skip_existing": skip_existing,
        "add_conditions": add_conditions,
    }


def _build_rsi_indicator_candidate(
    current_rules: dict[str, Any],
    signal_filter: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any] | None:
    raw_period = signal_filter.get("buy_rsi_period_line")
    raw_value = signal_filter.get("buy_rsi_value_line")
    if raw_period in (None, "") or raw_value in (None, ""):
        return None

    period = _safe_int(raw_period)
    operator = _compare_operator(signal_filter.get("buy_rsi_compare_combo"))
    threshold = _safe_float(raw_value)
    if period is None:
        warnings.append("buy RSI period is not numeric")
        return None
    if operator is None:
        warnings.append(f"buy RSI compare is not mapped: {signal_filter.get('buy_rsi_compare_combo')!r}")
        return None
    if threshold is None:
        warnings.append("buy RSI threshold is not numeric")
        return None

    current_rsi = _get_path_value(current_rules, RSI_INDICATOR_PATH)
    if not isinstance(current_rsi, dict):
        warnings.append("current indicators.rsi is not available; RSI candidate was not generated")
        return None

    engine_value = deepcopy(current_rsi)
    engine_value["period"] = period
    return {
        "path": RSI_INDICATOR_PATH,
        "value": engine_value,
        "ui_filter": {
            "period": period,
            "operator": operator,
            "threshold": threshold,
        },
    }


def build_engine_rules_preview_from_ui_state(
    ui_state: dict[str, Any],
    current_rules: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a preview-only engine rules candidate from saved UI state."""
    validation_warnings: list[str] = []
    postponed: list[str] = []
    legacy_notices: list[str] = []
    execution_locks: list[str] = []
    preview_rules = deepcopy(current_rules) if isinstance(current_rules, dict) else {}
    source_rules = current_rules if isinstance(current_rules, dict) else {}
    preview_rules["bar"] = {}
    preview_candidates: dict[str, Any] = {}
    state = _as_dict(ui_state)

    basic = _as_dict(state.get("basic"))
    bar_minutes = _safe_int(basic.get("basic_signal_interval_combo"))
    if bar_minutes is None:
        validation_warnings.append("basic signal interval is not numeric; bar.bar_minutes not mapped")
    else:
        preview_rules["bar"]["bar_minutes"] = bar_minutes
        preview_candidates["bar"] = {
            "path": BAR_MINUTES_PATH,
            "value": bar_minutes,
        }

    signal_runtime_policy_candidate = _build_signal_runtime_policy_candidate(basic, validation_warnings)
    if signal_runtime_policy_candidate:
        _set_path_value(preview_rules, SIGNAL_RUNTIME_POLICY_PATH, signal_runtime_policy_candidate["value"])
        preview_candidates["signal_runtime_policy"] = signal_runtime_policy_candidate

    buy_ui = _as_dict(state.get("buy_ui"))
    signal_filter = deepcopy(_as_dict(buy_ui.get("signal_filter")))
    signal_filter["signal_expression"] = basic.get("buy_signal_expr_line")
    price_compare = _as_dict(buy_ui.get("price_compare"))
    execution_base = _as_dict(buy_ui.get("base"))
    execution_repeat = _as_dict(buy_ui.get("repeat"))
    execution_additional = _as_dict(buy_ui.get("additional"))
    execution_cycle = _as_dict(buy_ui.get("cycle"))

    buy_ocr_filter_candidate = _build_buy_ocr_filter_candidate(signal_filter, validation_warnings)
    if buy_ocr_filter_candidate:
        _set_path_value(preview_rules, BUY_OCR_FILTER_PATH, buy_ocr_filter_candidate["value"])
        preview_candidates.setdefault("filters", {})["ocr"] = buy_ocr_filter_candidate

    buy_candidate = None
    buy_conditions = [] if buy_ocr_filter_candidate else _build_buy_osc_conditions(signal_filter, validation_warnings)
    if buy_conditions:
        buy_candidate = _build_buy_merge_candidate(source_rules, buy_conditions, validation_warnings)
        if buy_candidate:
            preview_candidates["buy"] = buy_candidate
    elif buy_ocr_filter_candidate:
        legacy_notices.append("legacy buy OCR/OSC merge candidate skipped because buy.filters.ocr is available")
    else:
        validation_warnings.append("buy OCR/OSC candidate group was not generated")

    buy_ma_filter_candidate = _build_buy_ma_filter_candidate(signal_filter, validation_warnings)
    if buy_ma_filter_candidate:
        _set_path_value(preview_rules, BUY_MOVING_AVERAGE_FILTER_PATH, buy_ma_filter_candidate["value"])
        preview_candidates.setdefault("filters", {})["moving_average"] = buy_ma_filter_candidate

    buy_bollinger_filter_candidate = _build_buy_bollinger_filter_candidate(signal_filter, validation_warnings)
    if buy_bollinger_filter_candidate:
        _set_path_value(preview_rules, BUY_BOLLINGER_FILTER_PATH, buy_bollinger_filter_candidate["value"])
        preview_candidates.setdefault("filters", {})["bollinger"] = buy_bollinger_filter_candidate

    buy_price_compare_filter_candidate = _build_buy_price_compare_filter_candidate(price_compare, validation_warnings)
    if buy_price_compare_filter_candidate:
        _set_path_value(preview_rules, BUY_PRICE_COMPARE_FILTER_PATH, buy_price_compare_filter_candidate["value"])
        preview_candidates.setdefault("filters", {})["price_compare"] = buy_price_compare_filter_candidate

    buy_rsi_filter_candidate = _build_buy_rsi_filter_candidate(signal_filter, validation_warnings)
    if buy_rsi_filter_candidate:
        _set_path_value(preview_rules, BUY_RSI_FILTER_PATH, buy_rsi_filter_candidate["value"])
        preview_candidates.setdefault("filters", {})["rsi"] = buy_rsi_filter_candidate

    buy_composite_filter_candidate = _build_buy_composite_filter_candidate(signal_filter, validation_warnings)
    if buy_composite_filter_candidate:
        _set_path_value(preview_rules, BUY_COMPOSITE_FILTER_PATH, buy_composite_filter_candidate["value"])
        preview_candidates.setdefault("filters", {})["composite"] = buy_composite_filter_candidate

    buy_execution_base_candidate = _build_buy_execution_base_candidate(execution_base, validation_warnings)
    buy_exit_policy = _build_buy_exit_policy(_as_dict(buy_ui.get("exit")), validation_warnings)
    # Situation timeout belongs to the existing base execution policy and
    # therefore follows the same Pending/Approval/Commit path.
    situation = _as_dict(buy_ui.get("situation"))
    old_base = _as_dict(_as_dict(_as_dict(source_rules.get("buy")).get("execution")).get("base"))
    if not buy_execution_base_candidate and old_base and ("type_combo" in situation or buy_exit_policy is not None):
        buy_execution_base_candidate = {"path": BUY_EXECUTION_BASE_PATH,
            "operation": "set_execution_policy", "value": deepcopy(old_base),
            "execution_connected": True,
            "execution_lock_reason": ""}
    if buy_execution_base_candidate:
        value = buy_execution_base_candidate["value"]
        if "type_combo" not in situation and "unfilled_timeout_policy" in old_base:
            value["unfilled_timeout_policy"] = deepcopy(old_base["unfilled_timeout_policy"])
        if "type_combo" not in situation and "buy_price_reset_policy" in old_base:
            value["buy_price_reset_policy"] = deepcopy(old_base["buy_price_reset_policy"])
        elif situation.get("type_combo") == "미체결":
            value["unfilled_timeout_policy"] = {
                "policy": "CANCEL_PENDING_ORDER", "enabled": True, "action": "CANCEL",
                "scope": {"매회": "EACH", "일괄": "BATCH"}.get(situation.get("unfilled_scope_combo")),
                "configured_value": _safe_float(situation.get("unfilled_time_line")),
                "configured_unit": _point_unit_token(situation.get("unfilled_unit_combo")),
                "anchor": "BROKER_ACCEPTED_AT",
            }
        elif "type_combo" in situation:
            # Price reset/cancel is still reserved; do not retain an active
            # timeout from a previously selected situation page.
            value["unfilled_timeout_policy"] = {"policy": "CANCEL_PENDING_ORDER", "enabled": False}
        if situation.get("type_combo") == "가격비교" and situation.get("action_combo") == "매수리셋":
            value["buy_price_reset_policy"] = {
                "policy": "BUY_PRICE_CHANGE_RESET",
                "enabled": True,
                "action": "RESET",
                "left_source": _price_basis_token(situation.get("left_combo")),
                "right_source": _price_basis_token(situation.get("right_combo")),
                "direction": _direction_token(situation.get("direction_combo")),
                "threshold_percent": _safe_float(situation.get("ratio_line")),
                "compare": _ratio_compare_token(situation.get("compare_combo")),
            }
        elif "type_combo" in situation:
            value["buy_price_reset_policy"] = {
                "policy": "BUY_PRICE_CHANGE_RESET", "enabled": False,
            }
        if buy_exit_policy is not None:
            value["buy_exit_policy"] = deepcopy(buy_exit_policy)
        elif "exit" in buy_ui and "buy_exit_policy" in old_base:
            # Preserve an existing canonical policy when this UI snapshot did
            # not include editable exit controls.
            value["buy_exit_policy"] = deepcopy(old_base["buy_exit_policy"])
    if buy_execution_base_candidate:
        _set_buy_execution_policy_value(
            preview_rules,
            BUY_EXECUTION_BASE_PATH,
            buy_execution_base_candidate["value"],
        )
        preview_candidates.setdefault("execution", {})["base"] = buy_execution_base_candidate
        if buy_execution_base_candidate.get("execution_connected") is False:
            execution_locks.append(
                f"{BUY_EXECUTION_BASE_PATH}: "
                f"{buy_execution_base_candidate.get('execution_lock_reason') or P1_EXECUTION_LOCK_REASON}"
            )

    buy_execution_repeat_candidate = _build_buy_execution_repeat_candidate(
        execution_repeat,
        validation_warnings,
        legacy_base=execution_base,
    )
    if buy_execution_repeat_candidate:
        _set_buy_execution_policy_value(
            preview_rules,
            BUY_EXECUTION_REPEAT_PATH,
            buy_execution_repeat_candidate["value"],
        )
        preview_candidates.setdefault("execution", {})["repeat"] = buy_execution_repeat_candidate

    buy_execution_additional_candidate = _build_buy_execution_additional_candidate(
        execution_additional,
        validation_warnings,
    )
    if buy_execution_additional_candidate:
        _set_buy_execution_policy_value(
            preview_rules,
            BUY_EXECUTION_ADDITIONAL_PATH,
            buy_execution_additional_candidate["value"],
        )
        preview_candidates.setdefault("execution", {})["additional"] = buy_execution_additional_candidate
        if buy_execution_additional_candidate.get("execution_connected") is False:
            execution_locks.append(
                f"{BUY_EXECUTION_ADDITIONAL_PATH}: "
                f"{buy_execution_additional_candidate.get('execution_lock_reason') or P1_EXECUTION_LOCK_REASON}"
            )

    buy_execution_cycle_candidate = _build_buy_execution_cycle_candidate(
        execution_cycle,
        validation_warnings,
    )
    if buy_execution_cycle_candidate:
        _set_buy_execution_policy_value(
            preview_rules,
            BUY_EXECUTION_CYCLE_PATH,
            buy_execution_cycle_candidate["value"],
        )
        preview_candidates.setdefault("execution", {})["cycle"] = buy_execution_cycle_candidate
        if buy_execution_cycle_candidate.get("execution_connected") is False:
            execution_locks.append(
                f"{BUY_EXECUTION_CYCLE_PATH}: "
                f"{buy_execution_cycle_candidate.get('execution_lock_reason') or CYCLE_OPTION_EXECUTION_LOCK_REASON}"
            )

    rsi_candidate = _build_rsi_indicator_candidate(source_rules, signal_filter, validation_warnings)
    if rsi_candidate:
        indicators_section = preview_rules.setdefault("indicators", {})
        if isinstance(indicators_section, dict):
            indicators_section["rsi"] = deepcopy(rsi_candidate["value"])
        preview_candidates["indicators"] = {
            "rsi": rsi_candidate,
        }

    sell_ui = _as_dict(state.get("sell_ui"))
    sell_method_candidates = _build_sell_method_policy_candidates(sell_ui, validation_warnings)
    if sell_method_candidates:
        method_preview = preview_rules.setdefault("sell", {}).setdefault("method", {})
        if isinstance(method_preview, dict):
            for path, candidate in sell_method_candidates.items():
                value = _method_policy_value(candidate)
                if value is not _MISSING:
                    _set_path_value(preview_rules, path, value)

    sell_profit_rate_candidate = _build_sell_profit_rate_signal_candidate(sell_ui, validation_warnings)
    if sell_profit_rate_candidate:
        _set_path_value(preview_rules, SELL_PROFIT_RATE_SIGNAL_PATH, sell_profit_rate_candidate["value"])

    signal_conditions = _as_dict(sell_ui.get("signal_conditions"))
    sell_add_signal_candidates: dict[str, dict[str, Any]] = {}
    condition_a = _as_dict(signal_conditions.get("condition_a"))
    sell_condition_a_candidate = _build_sell_condition_a_signal_candidate(condition_a, validation_warnings)
    if sell_condition_a_candidate:
        sell_add_signal_candidates[SELL_CONDITION_A_SIGNAL_PREVIEW_PATH] = sell_condition_a_candidate
        legacy_notices.append("sell condition A is an add_signal_candidate and does not replace existing macd_sell")
    elif condition_a:
        validation_warnings.append("sell condition A candidate group was not generated")

    condition_b = _as_dict(signal_conditions.get("condition_b"))
    sell_condition_b_candidate = _build_sell_condition_b_signal_candidate(condition_b, validation_warnings)
    if sell_condition_b_candidate:
        sell_add_signal_candidates[SELL_CONDITION_B_SIGNAL_PREVIEW_PATH] = sell_condition_b_candidate
        legacy_notices.append("sell condition B is an add_signal_candidate and does not replace existing macd_sell")
    elif condition_b:
        validation_warnings.append("sell condition B candidate group was not generated")

    condition_c = _as_dict(signal_conditions.get("condition_c"))
    sell_condition_c_candidate = _build_sell_condition_c_signal_candidate(condition_c, validation_warnings)
    if sell_condition_c_candidate:
        sell_add_signal_candidates[SELL_CONDITION_C_SIGNAL_PREVIEW_PATH] = sell_condition_c_candidate
        legacy_notices.append("sell condition C is an add_signal_candidate and does not replace existing macd_sell")
    else:
        validation_warnings.append("sell condition C candidate group was not generated")

    _attach_sell_signal_expression(
        sell_add_signal_candidates,
        basic.get("sell_signal_expr_line"),
        validation_warnings,
    )

    if sell_add_signal_candidates or sell_profit_rate_candidate or sell_method_candidates:
        sell_candidates: dict[str, Any] = {}
        if sell_method_candidates:
            sell_candidates["method_policy_candidates"] = sell_method_candidates
        if sell_profit_rate_candidate:
            sell_candidates["set_signal_candidates"] = {
                SELL_PROFIT_RATE_SIGNAL_PATH: sell_profit_rate_candidate,
            }
        if sell_add_signal_candidates:
            sell_candidates.update({
                "add_signal_candidate": next(iter(sell_add_signal_candidates.values())),
                "add_signal_candidates": sell_add_signal_candidates,
            })
        preview_candidates["sell"] = sell_candidates

    preview_rules["indicator_follow_rule_preview"] = {
        "mode": "merge_add_candidate",
        "candidates": preview_candidates,
        "reserved_controls": [],
    }

    if not buy_execution_base_candidate:
        postponed.append("buy method mapping is postponed")
    if not buy_execution_repeat_candidate:
        postponed.append("repeat buy mapping is postponed")
    postponed.append("completion policy mapping is postponed")

    mapped_paths = [
        BAR_MINUTES_PATH,
    ]
    if signal_runtime_policy_candidate:
        mapped_paths.append(SIGNAL_RUNTIME_POLICY_PATH)
    if buy_candidate:
        mapped_paths.append(BUY_CONDITIONS_PATH)
    if buy_ma_filter_candidate:
        mapped_paths.append(BUY_MOVING_AVERAGE_FILTER_PATH)
    if buy_bollinger_filter_candidate:
        mapped_paths.append(BUY_BOLLINGER_FILTER_PATH)
    if buy_ocr_filter_candidate:
        mapped_paths.append(BUY_OCR_FILTER_PATH)
    if buy_price_compare_filter_candidate:
        mapped_paths.append(BUY_PRICE_COMPARE_FILTER_PATH)
    if buy_rsi_filter_candidate:
        mapped_paths.append(BUY_RSI_FILTER_PATH)
    if buy_composite_filter_candidate:
        mapped_paths.append(BUY_COMPOSITE_FILTER_PATH)
    if buy_execution_base_candidate:
        mapped_paths.append(BUY_EXECUTION_BASE_PATH)
    if buy_execution_repeat_candidate:
        mapped_paths.append(BUY_EXECUTION_REPEAT_PATH)
    if buy_execution_additional_candidate:
        mapped_paths.append(BUY_EXECUTION_ADDITIONAL_PATH)
    if buy_execution_cycle_candidate:
        mapped_paths.append(BUY_EXECUTION_CYCLE_PATH)
    if sell_profit_rate_candidate:
        mapped_paths.append(SELL_PROFIT_RATE_SIGNAL_PATH)
    mapped_paths.extend(sell_method_candidates.keys())
    mapped_paths.extend([
        RSI_INDICATOR_PATH,
    ])
    mapped_paths.extend(sell_add_signal_candidates.keys())

    warnings = list(validation_warnings) + list(postponed) + list(execution_locks)
    return {
        "preview_rules": preview_rules,
        "mapped_paths": mapped_paths,
        "validation_warnings": validation_warnings,
        "postponed": postponed,
        "legacy_notices": legacy_notices,
        "execution_locks": execution_locks,
        "warnings": warnings,
    }


def build_engine_rules_pending_from_ui_state(
    ui_state: dict[str, Any],
    current_rules: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a pending namespace from preview candidates without saving files."""
    preview_result = build_engine_rules_preview_from_ui_state(ui_state, current_rules)
    preview_rules = _as_dict(preview_result.get("preview_rules"))
    preview_namespace = _as_dict(preview_rules.get("indicator_follow_rule_preview"))
    pending_rules = deepcopy(current_rules) if isinstance(current_rules, dict) else {}
    pending_rules["indicator_follow_rule_pending"] = {
        "version": "0.1",
        "source": "indicator_follow_ui_state",
        "source_ui_state_hash": build_ui_state_hash(ui_state),
        "mode": preview_namespace.get("mode", "merge_add_candidate"),
        "mapped_paths": list(preview_result.get("mapped_paths", [])),
        "candidates": deepcopy(_as_dict(preview_namespace.get("candidates"))),
        "execution_locks": list(preview_result.get("execution_locks", [])),
        "warnings": list(preview_result.get("warnings", [])),
    }
    return {
        "pending_rules": pending_rules,
        "preview_result": preview_result,
        "validation_warnings": list(preview_result.get("validation_warnings", [])),
        "postponed": list(preview_result.get("postponed", [])),
        "legacy_notices": list(preview_result.get("legacy_notices", [])),
        "execution_locks": list(preview_result.get("execution_locks", [])),
        "warnings": list(preview_result.get("warnings", [])),
    }


def _preview_rules_payload(preview_result: dict[str, Any]) -> dict[str, Any]:
    preview = _as_dict(preview_result)
    if isinstance(preview.get("preview_rules"), dict):
        return preview["preview_rules"]
    return _as_dict(preview.get("preview"))


def _preview_candidate_namespace(preview_result: dict[str, Any]) -> dict[str, Any]:
    preview_rules = _preview_rules_payload(preview_result)
    return _as_dict(preview_rules.get("indicator_follow_rule_preview"))


def _candidate_paths_from_preview(preview_result: dict[str, Any]) -> dict[str, str]:
    candidates = _as_dict(_preview_candidate_namespace(preview_result).get("candidates"))
    candidate_paths: dict[str, str] = {}

    bar_candidate = _as_dict(candidates.get("bar"))
    if bar_candidate:
        bar_path = str(bar_candidate.get("path") or BAR_MINUTES_PATH)
        candidate_paths[bar_path] = "set_value"

    signal_policy_candidate = _as_dict(candidates.get("signal_runtime_policy"))
    if signal_policy_candidate:
        policy_path = str(signal_policy_candidate.get("path") or SIGNAL_RUNTIME_POLICY_PATH)
        candidate_paths[policy_path] = "set_signal_runtime_policy"

    buy_candidate = _as_dict(candidates.get("buy"))
    if buy_candidate:
        merge_path = str(buy_candidate.get("merge_into") or BUY_CONDITIONS_PATH)
        candidate_paths[merge_path] = "merge_conditions"

    buy_ma_filter = _as_dict(_as_dict(candidates.get("filters")).get("moving_average"))
    if buy_ma_filter:
        filter_path = str(buy_ma_filter.get("path") or BUY_MOVING_AVERAGE_FILTER_PATH)
        candidate_paths[filter_path] = "set_filter"

    buy_bollinger_filter = _as_dict(_as_dict(candidates.get("filters")).get("bollinger"))
    if buy_bollinger_filter:
        filter_path = str(buy_bollinger_filter.get("path") or BUY_BOLLINGER_FILTER_PATH)
        candidate_paths[filter_path] = "set_filter"

    buy_ocr_filter = _as_dict(_as_dict(candidates.get("filters")).get("ocr"))
    if buy_ocr_filter:
        filter_path = str(buy_ocr_filter.get("path") or BUY_OCR_FILTER_PATH)
        candidate_paths[filter_path] = "set_filter"

    buy_rsi_filter = _as_dict(_as_dict(candidates.get("filters")).get("rsi"))
    if buy_rsi_filter:
        filter_path = str(buy_rsi_filter.get("path") or BUY_RSI_FILTER_PATH)
        candidate_paths[filter_path] = "set_filter"

    buy_composite_filter = _as_dict(_as_dict(candidates.get("filters")).get("composite"))
    if buy_composite_filter:
        filter_path = str(buy_composite_filter.get("path") or BUY_COMPOSITE_FILTER_PATH)
        candidate_paths[filter_path] = "set_filter"

    buy_price_compare_filter = _as_dict(_as_dict(candidates.get("filters")).get("price_compare"))
    if buy_price_compare_filter:
        filter_path = str(buy_price_compare_filter.get("path") or BUY_PRICE_COMPARE_FILTER_PATH)
        candidate_paths[filter_path] = "set_filter"

    buy_execution_base = _as_dict(_as_dict(candidates.get("execution")).get("base"))
    if buy_execution_base:
        execution_path = str(buy_execution_base.get("path") or BUY_EXECUTION_BASE_PATH)
        candidate_paths[execution_path] = "set_execution_policy"

    buy_execution_repeat = _as_dict(_as_dict(candidates.get("execution")).get("repeat"))
    if buy_execution_repeat:
        execution_path = str(buy_execution_repeat.get("path") or BUY_EXECUTION_REPEAT_PATH)
        candidate_paths[execution_path] = "set_execution_policy"

    buy_execution_additional = _as_dict(_as_dict(candidates.get("execution")).get("additional"))
    if buy_execution_additional:
        execution_path = str(buy_execution_additional.get("path") or BUY_EXECUTION_ADDITIONAL_PATH)
        candidate_paths[execution_path] = "set_execution_policy"

    buy_execution_cycle = _as_dict(_as_dict(candidates.get("execution")).get("cycle"))
    if buy_execution_cycle:
        execution_path = str(buy_execution_cycle.get("path") or BUY_EXECUTION_CYCLE_PATH)
        candidate_paths[execution_path] = "set_execution_policy"

    rsi_candidate = _as_dict(_as_dict(candidates.get("indicators")).get("rsi"))
    if rsi_candidate:
        rsi_path = str(rsi_candidate.get("path") or RSI_INDICATOR_PATH)
        candidate_paths[rsi_path] = "set_indicator"

    sell_candidates = _as_dict(candidates.get("sell"))
    method_policy_candidates = _as_dict(sell_candidates.get("method_policy_candidates"))
    for method_path, method_candidate in method_policy_candidates.items():
        if isinstance(method_candidate, dict):
            candidate_paths[str(method_path)] = "set_method_policy"

    set_signal_candidates = _as_dict(sell_candidates.get("set_signal_candidates"))
    for signal_path, signal_candidate in set_signal_candidates.items():
        if isinstance(signal_candidate, dict):
            candidate_paths[str(signal_path)] = "set_signal"

    add_signal_candidates = _as_dict(sell_candidates.get("add_signal_candidates"))
    if add_signal_candidates:
        for signal_path, sell_candidate in add_signal_candidates.items():
            if isinstance(sell_candidate, dict):
                candidate_paths[str(signal_path)] = "add_signal"
    else:
        sell_candidate = _as_dict(sell_candidates.get("add_signal_candidate"))
        if sell_candidate:
            signal_path = str(sell_candidate.get("path") or SELL_CONDITION_C_SIGNAL_PREVIEW_PATH)
            candidate_paths[signal_path] = "add_signal"

    return candidate_paths


def _decision_map(approval_decisions: Any) -> dict[str, str]:
    if approval_decisions is None:
        return {}
    if not isinstance(approval_decisions, dict):
        raise ValueError("approval_decisions must be a dict")

    source = approval_decisions.get("candidate_decisions")
    if isinstance(source, dict):
        decisions: dict[str, str] = {}
        for path, value in source.items():
            if isinstance(value, dict):
                decisions[str(path)] = str(value.get("decision", "PENDING"))
            else:
                decisions[str(path)] = str(value)
        return decisions

    return {str(path): str(decision) for path, decision in approval_decisions.items()}


def _validate_rule_candidate_decision(path: str, decision: str) -> None:
    if decision not in _RULE_CANDIDATE_DECISIONS:
        raise ValueError(f"unknown approval decision for {path}: {decision}")


def build_rule_approval_session_fingerprint(
    current_rules: dict[str, Any],
    preview_result: dict[str, Any],
) -> dict[str, Any]:
    """Build a stable fingerprint for approval-session restore checks."""
    rules = deepcopy(current_rules) if isinstance(current_rules, dict) else {}
    preview = deepcopy(preview_result) if isinstance(preview_result, dict) else {}
    preview_namespace = _preview_candidate_namespace(preview)
    candidate_paths = _candidate_paths_from_preview(preview)
    candidate_path_list = list(candidate_paths.keys())
    candidates = deepcopy(_as_dict(preview_namespace.get("candidates")))
    mapped_paths = list(_as_list(preview.get("mapped_paths")))
    current_rule_targets = {
        BAR_MINUTES_PATH: _get_path_value(rules, BAR_MINUTES_PATH),
        SIGNAL_RUNTIME_POLICY_PATH: _get_path_value(rules, SIGNAL_RUNTIME_POLICY_PATH),
        BUY_CONDITIONS_PATH: _get_path_value(rules, BUY_CONDITIONS_PATH),
        BUY_MOVING_AVERAGE_FILTER_PATH: _get_path_value(rules, BUY_MOVING_AVERAGE_FILTER_PATH),
        BUY_BOLLINGER_FILTER_PATH: _get_path_value(rules, BUY_BOLLINGER_FILTER_PATH),
        BUY_OCR_FILTER_PATH: _get_path_value(rules, BUY_OCR_FILTER_PATH),
        BUY_RSI_FILTER_PATH: _get_path_value(rules, BUY_RSI_FILTER_PATH),
        BUY_COMPOSITE_FILTER_PATH: _get_path_value(rules, BUY_COMPOSITE_FILTER_PATH),
        BUY_PRICE_COMPARE_FILTER_PATH: _get_path_value(rules, BUY_PRICE_COMPARE_FILTER_PATH),
        BUY_EXECUTION_BASE_PATH: _get_path_value(rules, BUY_EXECUTION_BASE_PATH),
        BUY_EXECUTION_REPEAT_PATH: _get_path_value(rules, BUY_EXECUTION_REPEAT_PATH),
        BUY_EXECUTION_ADDITIONAL_PATH: _get_path_value(rules, BUY_EXECUTION_ADDITIONAL_PATH),
        BUY_EXECUTION_CYCLE_PATH: _get_path_value(rules, BUY_EXECUTION_CYCLE_PATH),
        SELL_METHOD_SELECTED_SETS_PATH: _get_path_value(rules, SELL_METHOD_SELECTED_SETS_PATH),
        SELL_METHOD_SETTING_A_PATH: _get_path_value(rules, SELL_METHOD_SETTING_A_PATH),
        SELL_METHOD_SETTING_B_PATH: _get_path_value(rules, SELL_METHOD_SETTING_B_PATH),
        SELL_METHOD_SETTING_C_PATH: _get_path_value(rules, SELL_METHOD_SETTING_C_PATH),
        RSI_INDICATOR_PATH: _get_path_value(rules, RSI_INDICATOR_PATH),
        "sell.signals.macd_sell": _get_path_value(rules, "sell.signals.macd_sell"),
        SELL_PROFIT_RATE_SIGNAL_PATH: _get_path_value(rules, SELL_PROFIT_RATE_SIGNAL_PATH),
        SELL_CONDITION_A_SIGNAL_TARGET_PATH: _get_path_value(rules, SELL_CONDITION_A_SIGNAL_TARGET_PATH),
        SELL_CONDITION_B_SIGNAL_TARGET_PATH: _get_path_value(rules, SELL_CONDITION_B_SIGNAL_TARGET_PATH),
        SELL_CONDITION_C_SIGNAL_TARGET_PATH: _get_path_value(rules, SELL_CONDITION_C_SIGNAL_TARGET_PATH),
        SELL_MACD_SIGNAL_TARGET_PATH: _get_path_value(rules, SELL_MACD_SIGNAL_TARGET_PATH),
    }
    normalized_targets = {
        path: {
            "exists": value is not _MISSING,
            "value": None if value is _MISSING else deepcopy(value),
        }
        for path, value in current_rule_targets.items()
    }
    candidate_payload = {
        "preview_mode": preview_namespace.get("mode"),
        "candidate_paths": candidate_path_list,
        "candidate_types": candidate_paths,
        "candidates": candidates,
        "mapped_paths": mapped_paths,
    }
    target_payload = {
        "current_rule_targets": normalized_targets,
    }
    candidate_hash = _stable_hash(candidate_payload)
    current_rule_target_hash = _stable_hash(target_payload)
    current_rules_hash = _stable_hash(rules)
    fingerprint_payload = {
        "candidate_hash": candidate_hash,
        "current_rule_target_hash": current_rule_target_hash,
        "current_rules_hash": current_rules_hash,
    }
    return {
        "mode": "approval_candidate_fingerprint",
        "preview_mode": preview_namespace.get("mode"),
        "candidate_paths": candidate_path_list,
        "candidate_types": candidate_paths,
        "candidate_hash": candidate_hash,
        "current_rule_target_hash": current_rule_target_hash,
        "current_rules_hash": current_rules_hash,
        "fingerprint": _stable_hash(fingerprint_payload),
    }


def validate_rule_approval_session_for_preview(
    session: dict[str, Any],
    current_rules: dict[str, Any],
    preview_result: dict[str, Any],
) -> dict[str, Any]:
    """Validate whether an approval session still matches the current preview."""
    session_copy = deepcopy(session) if isinstance(session, dict) else {}
    current_fingerprint = build_rule_approval_session_fingerprint(current_rules, preview_result)
    current_candidate_types = current_fingerprint.get("candidate_types", {})
    session_candidate_types = _as_dict(session_copy.get("candidate_types"))
    session_decisions = _as_dict(session_copy.get("decisions"))
    warnings: list[str] = []
    blocked_reasons: list[str] = []

    current_paths = list(current_fingerprint.get("candidate_paths", []))
    session_paths = list(session_decisions.keys())
    path_match = session_paths == current_paths
    type_match = session_candidate_types == current_candidate_types

    if not path_match:
        blocked_reasons.append("approval session candidate paths do not match current preview")
    if not type_match:
        blocked_reasons.append("approval session candidate types do not match current preview")

    for path, decision in session_decisions.items():
        if path not in current_candidate_types:
            continue
        if str(decision) not in _RULE_CANDIDATE_DECISIONS:
            warnings.append(f"unknown approval decision reset required for {path}: {decision}")

    session_fingerprint = session_copy.get("fingerprint")
    fingerprint_match = session_fingerprint == current_fingerprint.get("fingerprint")
    if session_fingerprint is None:
        fingerprint_match = False
        blocked_reasons.append("approval session fingerprint is missing")
    elif not fingerprint_match:
        blocked_reasons.append("approval session fingerprint does not match current preview")

    valid = path_match and type_match and fingerprint_match and not blocked_reasons
    return {
        "mode": "approval_session_validation",
        "valid": valid,
        "path_match": path_match,
        "type_match": type_match,
        "fingerprint_match": fingerprint_match,
        "current_fingerprint": current_fingerprint,
        "session_fingerprint": session_fingerprint,
        "blocked_reasons": blocked_reasons,
        "warnings": warnings,
    }


def restore_rule_approval_session_for_preview(
    saved_session: dict[str, Any],
    current_rules: dict[str, Any],
    preview_result: dict[str, Any],
) -> dict[str, Any]:
    """Restore decisions only when the saved approval session matches preview."""
    fingerprint = build_rule_approval_session_fingerprint(current_rules, preview_result)
    current_session = build_rule_approval_session(preview_result)
    current_session["fingerprint"] = fingerprint.get("fingerprint")
    current_session["fingerprint_detail"] = fingerprint

    validation = validate_rule_approval_session_for_preview(
        saved_session,
        current_rules,
        preview_result,
    )
    warnings = list(validation.get("warnings", []))
    if not validation.get("valid"):
        warnings.append("approval session fingerprint mismatch; decisions reset to PENDING")
        current_session["warnings"] = list(current_session.get("warnings", [])) + warnings
        current_session["restore_status"] = "RESET_TO_PENDING"
        current_session["validation"] = validation
        return current_session

    restored = deepcopy(current_session)
    saved_decisions = _as_dict(_as_dict(saved_session).get("decisions"))
    for path in list(restored.get("decisions", {}).keys()):
        decision = str(saved_decisions.get(path, "PENDING"))
        if decision not in _RULE_CANDIDATE_DECISIONS:
            restored["decisions"][path] = "PENDING"
            warnings.append(f"unknown approval decision ignored for {path}: {decision}")
        else:
            restored["decisions"][path] = decision
    restored["warnings"] = list(restored.get("warnings", [])) + warnings
    restored["restore_status"] = "RESTORED"
    restored["validation"] = validation
    restored["updated_at"] = _now_iso()
    return restored


def evaluate_rule_candidate_approval(
    preview_result: dict[str, Any],
    approval_decisions: Any,
) -> dict[str, Any]:
    """Return approval decisions for preview candidates without changing rules."""
    candidate_paths = _candidate_paths_from_preview(_as_dict(preview_result))
    decisions = _decision_map(approval_decisions)
    approved_paths: list[str] = []
    rejected_paths: list[str] = []
    deferred_paths: list[str] = []
    candidate_decisions: dict[str, dict[str, str]] = {}
    warnings: list[str] = []

    for path, decision in decisions.items():
        _validate_rule_candidate_decision(path, decision)
        if path not in candidate_paths:
            warnings.append(f"unknown approval path ignored: {path}")

    for path, candidate_type in candidate_paths.items():
        decision = decisions.get(path, "PENDING")
        candidate_decisions[path] = {
            "decision": decision,
            "candidate_type": candidate_type,
        }
        if decision == "APPROVED":
            approved_paths.append(path)
        elif decision == "REJECTED":
            rejected_paths.append(path)
        elif decision == "DEFERRED":
            deferred_paths.append(path)

    return {
        "mode": "candidate_approval",
        "status": "PENDING_REVIEW",
        "approved_paths": approved_paths,
        "rejected_paths": rejected_paths,
        "deferred_paths": deferred_paths,
        "candidate_decisions": candidate_decisions,
        "warnings": warnings,
    }


def build_rule_approval_session(
    preview_result: dict[str, Any],
    initial_decisions: Any = None,
) -> dict[str, Any]:
    """Build an in-memory approval session for preview candidates only."""
    candidate_paths = _candidate_paths_from_preview(_as_dict(preview_result))
    decisions = {path: "PENDING" for path in candidate_paths}
    candidate_types = {path: candidate_type for path, candidate_type in candidate_paths.items()}
    initial = _decision_map(initial_decisions)
    warnings: list[str] = []

    for path, decision in initial.items():
        _validate_rule_candidate_decision(path, decision)
        if path not in candidate_paths:
            warnings.append(f"unknown approval session path ignored: {path}")
            continue
        decisions[path] = decision

    return {
        "mode": "approval_session",
        "routine": "지표추종매매",
        "routine_key": "indicator_follow",
        "session_status": "ACTIVE",
        "decisions": decisions,
        "candidate_types": candidate_types,
        "updated_at": _now_iso(),
        "warnings": warnings,
    }


def update_rule_approval_session(
    session: dict[str, Any],
    path: str,
    decision: str,
) -> dict[str, Any]:
    """Return a copied approval session with one candidate decision updated."""
    target_path = str(path)
    target_decision = str(decision)
    _validate_rule_candidate_decision(target_path, target_decision)

    session_copy = deepcopy(session) if isinstance(session, dict) else {}
    decisions = _as_dict(session_copy.get("decisions"))
    if target_path not in decisions:
        raise ValueError(f"unknown approval session path: {target_path}")

    session_copy["mode"] = "approval_session"
    session_copy["session_status"] = session_copy.get("session_status") or "ACTIVE"
    decisions[target_path] = target_decision
    session_copy["decisions"] = decisions
    if not isinstance(session_copy.get("warnings"), list):
        session_copy["warnings"] = []
    session_copy["updated_at"] = _now_iso()
    return session_copy


def build_rule_pipeline_preview(
    current_rules: dict[str, Any],
    preview_result: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    """Build approval, patch, and apply previews from an approval session."""
    session_copy = deepcopy(session) if isinstance(session, dict) else {}
    decisions = deepcopy(_as_dict(session_copy.get("decisions")))
    approval_result = evaluate_rule_candidate_approval(preview_result, decisions)
    patch_preview = build_approved_rule_patch_preview(current_rules, preview_result, approval_result)
    apply_preview = apply_approved_rule_patch_preview(current_rules, patch_preview)
    warnings: list[str] = []
    for source in (
        session_copy.get("warnings"),
        approval_result.get("warnings"),
        patch_preview.get("warnings"),
        apply_preview.get("warnings"),
    ):
        if isinstance(source, list):
            warnings.extend(str(item) for item in source)

    return {
        "mode": "rule_pipeline_preview",
        "stage": "RULE_PIPELINE_PREVIEW",
        "session": session_copy,
        "approval_result": approval_result,
        "patch_preview": patch_preview,
        "apply_preview": apply_preview,
        "warnings": warnings,
    }


def _approval_decision_for_path(approval_result: dict[str, Any], path: str) -> str:
    decision = _as_dict(_as_dict(approval_result).get("candidate_decisions")).get(path)
    if isinstance(decision, dict):
        return str(decision.get("decision", "PENDING"))
    return "PENDING"


def _patch_skipped(path: str, reason: str) -> dict[str, str]:
    return {
        "path": path,
        "reason": reason,
    }


def build_approved_rule_patch_preview(
    current_rules: dict[str, Any],
    preview_result: dict[str, Any],
    approval_result: dict[str, Any],
) -> dict[str, Any]:
    """Build preview-only patch candidates from approved rule candidates."""
    current = _as_dict(current_rules)
    preview_candidates = _as_dict(_preview_candidate_namespace(_as_dict(preview_result)).get("candidates"))
    approval = _as_dict(approval_result)
    approved_paths_value = approval.get("approved_paths")
    approved_paths = [str(path) for path in approved_paths_value] if isinstance(approved_paths_value, list) else []
    candidate_paths = _candidate_paths_from_preview(_as_dict(preview_result))
    patches: list[dict[str, Any]] = []
    skipped_paths: list[dict[str, str]] = []
    warnings: list[str] = []

    for path, candidate_type in candidate_paths.items():
        decision = _approval_decision_for_path(approval, path)
        if decision != "APPROVED":
            skipped_paths.append(_patch_skipped(path, f"decision is {decision}"))

    for path in approved_paths:
        if path not in candidate_paths:
            skipped_paths.append(_patch_skipped(path, "approved path is not a preview candidate"))
            warnings.append(f"unknown approved path skipped: {path}")
            continue

        if path == BAR_MINUTES_PATH:
            bar_candidate = _as_dict(preview_candidates.get("bar"))
            if "value" not in bar_candidate:
                skipped_paths.append(_patch_skipped(path, "bar value is not available"))
                continue

            current_value = _get_path_value(current, BAR_MINUTES_PATH)
            new_value = bar_candidate.get("value")
            if current_value == new_value:
                skipped_paths.append(_patch_skipped(path, "bar.bar_minutes is unchanged"))
                continue

            patches.append({
                "source_path": BAR_MINUTES_PATH,
                "target_path": BAR_MINUTES_PATH,
                "operation": "set_value",
                "value": deepcopy(new_value),
                "risk": "low",
            })
            continue

        if path == SIGNAL_RUNTIME_POLICY_PATH:
            candidate = _as_dict(preview_candidates.get("signal_runtime_policy"))
            value = candidate.get("value")
            if not isinstance(value, dict):
                skipped_paths.append(_patch_skipped(path, "signal runtime policy value is not available"))
                continue
            if _get_path_value(current, path) == value:
                skipped_paths.append(_patch_skipped(path, "signal runtime policy is unchanged"))
                continue
            patches.append({
                "source_path": path,
                "target_path": path,
                "operation": "set_signal_runtime_policy",
                "value": deepcopy(value),
                "risk": "medium",
            })
            continue

        if path == RSI_INDICATOR_PATH:
            rsi_candidate = _as_dict(_as_dict(preview_candidates.get("indicators")).get("rsi"))
            candidate_value = rsi_candidate.get("value")
            if not isinstance(candidate_value, dict):
                skipped_paths.append(_patch_skipped(path, "RSI value is not available"))
                continue

            current_value = _get_path_value(current, RSI_INDICATOR_PATH)
            if current_value is _MISSING:
                skipped_paths.append(_patch_skipped(path, "current indicators.rsi is not available"))
                continue
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "RSI indicator is unchanged"))
                continue

            patches.append({
                "source_path": RSI_INDICATOR_PATH,
                "target_path": RSI_INDICATOR_PATH,
                "operation": "set_indicator",
                "value": deepcopy(candidate_value),
                "risk": "low",
            })
            continue

        if path == BUY_CONDITIONS_PATH:
            buy_candidate = _as_dict(preview_candidates.get("buy"))
            add_conditions = buy_candidate.get("add_conditions")
            if not isinstance(add_conditions, list):
                skipped_paths.append(_patch_skipped(path, "buy add_conditions is not available"))
                continue

            existing_conditions = _get_path_value(current, BUY_CONDITIONS_PATH)
            existing_list = existing_conditions if isinstance(existing_conditions, list) else []
            patch_add_conditions = [
                deepcopy(condition)
                for condition in add_conditions
                if isinstance(condition, dict)
                and not any(
                    isinstance(existing, dict) and _condition_matches(existing, condition)
                    for existing in existing_list
                )
            ]
            if not patch_add_conditions:
                skipped_paths.append(_patch_skipped(path, "no new buy conditions to merge"))
                continue

            patches.append({
                "source_path": BUY_CONDITIONS_PATH,
                "target_path": BUY_CONDITIONS_PATH,
                "operation": "merge_conditions",
                "add_conditions": patch_add_conditions,
                "skip_existing": deepcopy(_as_list(buy_candidate.get("skip_existing"))),
                "risk": "medium",
            })
            continue

        if path == BUY_MOVING_AVERAGE_FILTER_PATH:
            filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("moving_average"))
            candidate_value = _moving_average_filter_value(filter_candidate)
            if not candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY moving_average filter value is not available"))
                continue

            current_value = _get_path_value(current, BUY_MOVING_AVERAGE_FILTER_PATH)
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY moving_average filter is unchanged"))
                continue

            patches.append({
                "source_path": BUY_MOVING_AVERAGE_FILTER_PATH,
                "target_path": BUY_MOVING_AVERAGE_FILTER_PATH,
                "operation": "set_filter",
                "value": candidate_value,
                "risk": "low",
            })
            continue

        if path == BUY_PRICE_COMPARE_FILTER_PATH:
            filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("price_compare"))
            candidate_value = _price_compare_filter_value(filter_candidate)
            if not candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY price_compare filter value is not available"))
                continue

            current_value = _get_path_value(current, BUY_PRICE_COMPARE_FILTER_PATH)
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY price_compare filter is unchanged"))
                continue

            patches.append({
                "source_path": BUY_PRICE_COMPARE_FILTER_PATH,
                "target_path": BUY_PRICE_COMPARE_FILTER_PATH,
                "operation": "set_filter",
                "value": candidate_value,
                "risk": "low",
            })
            continue

        if path == BUY_BOLLINGER_FILTER_PATH:
            filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("bollinger"))
            candidate_value = _bollinger_filter_value(filter_candidate)
            if not candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY bollinger filter value is not available"))
                continue

            current_value = _get_path_value(current, BUY_BOLLINGER_FILTER_PATH)
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY bollinger filter is unchanged"))
                continue

            patches.append({
                "source_path": BUY_BOLLINGER_FILTER_PATH,
                "target_path": BUY_BOLLINGER_FILTER_PATH,
                "operation": "set_filter",
                "value": candidate_value,
                "risk": "low",
            })
            continue

        if path == BUY_OCR_FILTER_PATH:
            filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("ocr"))
            candidate_value = _ocr_filter_value(filter_candidate)
            if not candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY ocr filter value is not available"))
                continue

            current_value = _get_path_value(current, BUY_OCR_FILTER_PATH)
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY ocr filter is unchanged"))
                continue

            patches.append({
                "source_path": BUY_OCR_FILTER_PATH,
                "target_path": BUY_OCR_FILTER_PATH,
                "operation": "set_filter",
                "value": candidate_value,
                "risk": "low",
            })
            continue

        if path == BUY_RSI_FILTER_PATH:
            filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("rsi"))
            candidate_value = _rsi_filter_value(filter_candidate)
            if not candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY rsi filter value is not available"))
                continue

            current_value = _get_path_value(current, BUY_RSI_FILTER_PATH)
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY rsi filter is unchanged"))
                continue

            patches.append({
                "source_path": BUY_RSI_FILTER_PATH,
                "target_path": BUY_RSI_FILTER_PATH,
                "operation": "set_filter",
                "value": candidate_value,
                "risk": "low",
            })
            continue

        if path == BUY_COMPOSITE_FILTER_PATH:
            filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("composite"))
            candidate_value = _composite_filter_value(filter_candidate)
            if not candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY composite filter value is not available"))
                continue

            current_value = _get_path_value(current, BUY_COMPOSITE_FILTER_PATH)
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY composite filter is unchanged"))
                continue

            patches.append({
                "source_path": BUY_COMPOSITE_FILTER_PATH,
                "target_path": BUY_COMPOSITE_FILTER_PATH,
                "operation": "set_filter",
                "value": candidate_value,
                "risk": "low",
            })
            continue

        if path == BUY_EXECUTION_BASE_PATH:
            execution_candidate = _as_dict(_as_dict(preview_candidates.get("execution")).get("base"))
            if _execution_candidate_is_locked(execution_candidate):
                skipped_paths.append(_patch_skipped(
                    path,
                    execution_candidate.get("execution_lock_reason") or P1_EXECUTION_LOCK_REASON,
                ))
                continue
            candidate_value = _execution_policy_value(execution_candidate)
            if not candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY execution base value is not available"))
                continue

            current_value = _get_path_value(current, BUY_EXECUTION_BASE_PATH)
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY execution base policy is unchanged"))
                continue

            patches.append({
                "source_path": BUY_EXECUTION_BASE_PATH,
                "target_path": BUY_EXECUTION_BASE_PATH,
                "operation": "set_execution_policy",
                "value": candidate_value,
                "risk": "medium",
            })
            continue

        if path == BUY_EXECUTION_REPEAT_PATH:
            execution_candidate = _as_dict(_as_dict(preview_candidates.get("execution")).get("repeat"))
            candidate_value = _execution_policy_value(execution_candidate)
            if not candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY execution repeat value is not available"))
                continue

            current_value = _get_path_value(current, BUY_EXECUTION_REPEAT_PATH)
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY execution repeat policy is unchanged"))
                continue

            patches.append({
                "source_path": BUY_EXECUTION_REPEAT_PATH,
                "target_path": BUY_EXECUTION_REPEAT_PATH,
                "operation": "set_execution_policy",
                "value": candidate_value,
                "risk": "medium",
            })
            continue

        if path in {BUY_EXECUTION_ADDITIONAL_PATH, BUY_EXECUTION_CYCLE_PATH}:
            candidate_key = _BUY_EXECUTION_CANDIDATE_KEYS[path]
            execution_candidate = _as_dict(
                _as_dict(preview_candidates.get("execution")).get(candidate_key)
            )
            if _execution_candidate_is_locked(execution_candidate):
                skipped_paths.append(_patch_skipped(
                    path,
                    execution_candidate.get("execution_lock_reason") or P1_EXECUTION_LOCK_REASON,
                ))
                continue
            candidate_value = _execution_policy_value(execution_candidate)
            if not candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY execution policy value is not available"))
                continue
            current_value = _get_path_value(current, path)
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "BUY execution policy is unchanged"))
                continue
            patches.append({
                "source_path": path,
                "target_path": path,
                "operation": "set_execution_policy",
                "value": candidate_value,
                "risk": "medium",
            })
            continue

        if path in _SELL_METHOD_PATHS:
            method_candidate = _as_dict(_as_dict(_as_dict(preview_candidates.get("sell")).get("method_policy_candidates")).get(path))
            candidate_value = _method_policy_value(method_candidate)
            if candidate_value is _MISSING:
                skipped_paths.append(_patch_skipped(path, "SELL method policy value is not available"))
                continue

            current_value = _get_path_value(current, path)
            if current_value == candidate_value:
                skipped_paths.append(_patch_skipped(path, "SELL method policy is unchanged"))
                continue

            patches.append({
                "source_path": path,
                "target_path": path,
                "operation": "set_method_policy",
                "value": candidate_value,
                "risk": "medium",
            })
            continue

        if path == SELL_PROFIT_RATE_SIGNAL_PATH:
            signal_candidate = _sell_set_signal_candidate(preview_candidates, path)
            candidate_value = _profit_rate_signal_value(signal_candidate)
            if not candidate_value:
                skipped_paths.append(_patch_skipped(path, "profit_rate_sell signal value is not available"))
                continue

            current_value = _get_path_value(current, SELL_PROFIT_RATE_SIGNAL_PATH)
            current_signal = current_value if isinstance(current_value, dict) else {}
            current_allowed = {
                key: deepcopy(current_signal[key])
                for key in ("enabled", "profit_rate_percent", "basis")
                if key in current_signal
            }
            if current_allowed == candidate_value:
                skipped_paths.append(_patch_skipped(path, "profit_rate_sell signal is unchanged"))
                continue

            patches.append({
                "source_path": SELL_PROFIT_RATE_SIGNAL_PATH,
                "target_path": SELL_PROFIT_RATE_SIGNAL_PATH,
                "operation": "set_signal",
                "value": candidate_value,
                "allowed_fields": sorted(_SELL_PROFIT_RATE_ALLOWED_FIELDS),
                "risk": "medium",
            })
            continue

        sell_target = _sell_add_signal_target(path)
        if sell_target:
            target_path, _target_key = sell_target
            sell_candidate = _sell_add_signal_candidate(preview_candidates, path)
            if not sell_candidate:
                skipped_paths.append(_patch_skipped(path, "sell add_signal_candidate is not available"))
                continue

            signal = _sell_add_signal_payload(sell_candidate)
            signal.pop("path", None)
            signal.pop("candidate_type", None)
            signal.pop("preview_candidate", None)
            signal["enabled"] = signal.get("enabled") is True
            existing_signal = _get_path_value(current, target_path)
            if existing_signal is not _MISSING:
                if existing_signal == signal:
                    skipped_paths.append(_patch_skipped(path, "sell signal is unchanged"))
                else:
                    skipped_paths.append(_patch_skipped(path, f"target path already exists: {target_path}"))
                continue

            patches.append({
                "source_path": path,
                "target_path": target_path,
                "operation": "add_signal",
                "signal": signal,
                "risk": "high",
            })
            continue

        skipped_paths.append(_patch_skipped(path, "approved path has no patch builder"))
        warnings.append(f"approved path has no patch builder: {path}")

    return {
        "mode": "approved_rule_patch_preview",
        "stage": "RULE_PATCH_PREVIEW",
        "patches": patches,
        "summary": {
            "approved": len(approved_paths),
            "patches": len(patches),
            "skipped": len(skipped_paths),
        },
        "skipped_paths": skipped_paths,
        "warnings": warnings,
    }


def _apply_skipped(patch: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "source_path": patch.get("source_path"),
        "target_path": patch.get("target_path"),
        "operation": patch.get("operation"),
        "reason": reason,
    }


def apply_approved_rule_patch_preview(
    current_rules: dict[str, Any],
    patch_preview: dict[str, Any],
) -> dict[str, Any]:
    """Apply approved patch candidates to a copied rules dict for preview only."""
    applied_rules_preview = deepcopy(current_rules) if isinstance(current_rules, dict) else {}
    patch_source = _as_dict(patch_preview)
    patches_value = patch_source.get("patches")
    patches = patches_value if isinstance(patches_value, list) else []
    applied_patches: list[dict[str, Any]] = []
    skipped_patches: list[dict[str, Any]] = []
    warnings: list[str] = []

    for patch_value in patches:
        patch = _as_dict(patch_value)
        operation = str(patch.get("operation") or "")
        target_path = str(patch.get("target_path") or "")

        if operation == "set_value":
            if target_path != BAR_MINUTES_PATH:
                skipped_patches.append(_apply_skipped(patch, "unsupported set_value target path"))
                warnings.append(f"unsupported set_value target path: {target_path}")
                continue

            bar_section = applied_rules_preview.setdefault("bar", {})
            if not isinstance(bar_section, dict):
                skipped_patches.append(_apply_skipped(patch, "bar section is not a dict"))
                continue

            bar_section["bar_minutes"] = deepcopy(patch.get("value"))
            applied_patches.append({
                "source_path": patch.get("source_path"),
                "target_path": target_path,
                "operation": operation,
            })
            continue

        if operation == "set_indicator":
            if target_path != RSI_INDICATOR_PATH:
                skipped_patches.append(_apply_skipped(patch, "unsupported set_indicator target path"))
                warnings.append(f"unsupported set_indicator target path: {target_path}")
                continue

            indicators_section = applied_rules_preview.setdefault("indicators", {})
            if not isinstance(indicators_section, dict):
                skipped_patches.append(_apply_skipped(patch, "indicators section is not a dict"))
                continue

            value = patch.get("value")
            if not isinstance(value, dict):
                skipped_patches.append(_apply_skipped(patch, "indicator value is not a dict"))
                continue

            indicators_section["rsi"] = deepcopy(value)
            applied_patches.append({
                "source_path": patch.get("source_path"),
                "target_path": target_path,
                "operation": operation,
            })
            continue

        if operation == "set_signal_runtime_policy":
            value = patch.get("value")
            if target_path != SIGNAL_RUNTIME_POLICY_PATH or not isinstance(value, dict):
                skipped_patches.append(_apply_skipped(patch, "unsupported signal runtime policy"))
                continue
            if not _set_path_value(applied_rules_preview, target_path, value):
                skipped_patches.append(_apply_skipped(patch, "signal runtime policy path is not writable"))
                continue
            applied_patches.append({
                "source_path": patch.get("source_path"),
                "target_path": target_path,
                "operation": operation,
            })
            continue

        if operation == "merge_conditions":
            if target_path != BUY_CONDITIONS_PATH:
                skipped_patches.append(_apply_skipped(patch, "unsupported merge target path"))
                warnings.append(f"unsupported merge target path: {target_path}")
                continue

            conditions = _get_path_value(applied_rules_preview, BUY_CONDITIONS_PATH)
            if not isinstance(conditions, list):
                skipped_patches.append(_apply_skipped(patch, "target conditions are not available"))
                continue

            add_conditions = patch.get("add_conditions")
            if not isinstance(add_conditions, list):
                skipped_patches.append(_apply_skipped(patch, "add_conditions is not a list"))
                continue

            added_count = 0
            for condition in add_conditions:
                if not isinstance(condition, dict):
                    continue
                if any(isinstance(existing, dict) and _condition_matches(existing, condition) for existing in conditions):
                    continue
                conditions.append(deepcopy(condition))
                added_count += 1

            if added_count == 0:
                skipped_patches.append(_apply_skipped(patch, "no new conditions to add"))
                continue

            applied_patches.append({
                "source_path": patch.get("source_path"),
                "target_path": target_path,
                "operation": operation,
                "added_count": added_count,
            })
            continue

        if operation == "set_filter":
            if target_path not in {
                BUY_MOVING_AVERAGE_FILTER_PATH,
                BUY_PRICE_COMPARE_FILTER_PATH,
                BUY_BOLLINGER_FILTER_PATH,
                BUY_OCR_FILTER_PATH,
                BUY_RSI_FILTER_PATH,
                BUY_COMPOSITE_FILTER_PATH,
            }:
                skipped_patches.append(_apply_skipped(patch, "unsupported filter target path"))
                warnings.append(f"unsupported filter target path: {target_path}")
                continue

            value = patch.get("value")
            if not isinstance(value, dict):
                skipped_patches.append(_apply_skipped(patch, "filter value is not a dict"))
                continue
            if not _set_path_value(applied_rules_preview, target_path, value):
                skipped_patches.append(_apply_skipped(patch, "target filter path is not writable"))
                continue

            applied_patches.append({
                "source_path": patch.get("source_path"),
                "target_path": target_path,
                "operation": operation,
            })
            continue

        if operation == "set_execution_policy":
            if target_path not in _BUY_EXECUTION_PATHS:
                skipped_patches.append(_apply_skipped(patch, "unsupported execution policy target path"))
                warnings.append(f"unsupported execution policy target path: {target_path}")
                continue

            value = patch.get("value")
            if not isinstance(value, dict):
                skipped_patches.append(_apply_skipped(patch, "execution policy value is not a dict"))
                continue
            if not _set_buy_execution_policy_value(
                applied_rules_preview,
                target_path,
                value,
            ):
                skipped_patches.append(_apply_skipped(patch, "target execution policy path is not writable"))
                continue

            applied_patches.append({
                "source_path": patch.get("source_path"),
                "target_path": target_path,
                "operation": operation,
            })
            continue

        if operation == "set_method_policy":
            if target_path not in _SELL_METHOD_PATHS:
                skipped_patches.append(_apply_skipped(patch, "unsupported method policy target path"))
                warnings.append(f"unsupported method policy target path: {target_path}")
                continue
            if "value" not in patch:
                skipped_patches.append(_apply_skipped(patch, "method policy value is not available"))
                continue
            if not _set_path_value(applied_rules_preview, target_path, patch.get("value")):
                skipped_patches.append(_apply_skipped(patch, "target method policy path is not writable"))
                continue

            applied_patches.append({
                "source_path": patch.get("source_path"),
                "target_path": target_path,
                "operation": operation,
            })
            continue

        if operation == "set_signal":
            if target_path != SELL_PROFIT_RATE_SIGNAL_PATH:
                skipped_patches.append(_apply_skipped(patch, "unsupported signal target path"))
                warnings.append(f"unsupported signal target path: {target_path}")
                continue

            value = patch.get("value")
            if not isinstance(value, dict):
                skipped_patches.append(_apply_skipped(patch, "signal value is not a dict"))
                continue
            if any(key not in _SELL_PROFIT_RATE_ALLOWED_FIELDS for key in value):
                skipped_patches.append(_apply_skipped(patch, "signal value contains unsupported fields"))
                continue

            sell_section = applied_rules_preview.setdefault("sell", {})
            if not isinstance(sell_section, dict):
                skipped_patches.append(_apply_skipped(patch, "sell section is not a dict"))
                continue
            signals = sell_section.setdefault("signals", {})
            if not isinstance(signals, dict):
                skipped_patches.append(_apply_skipped(patch, "sell.signals is not a dict"))
                continue
            profit_signal = signals.setdefault("profit_rate_sell", {})
            if not isinstance(profit_signal, dict):
                skipped_patches.append(_apply_skipped(patch, "profit_rate_sell signal is not a dict"))
                continue

            for key in ("enabled", "profit_rate_percent", "basis"):
                if key in value:
                    profit_signal[key] = deepcopy(value[key])
            applied_patches.append({
                "source_path": patch.get("source_path"),
                "target_path": target_path,
                "operation": operation,
            })
            continue

        if operation == "add_signal":
            sell_target_key = next(
                (
                    signal_key
                    for allowed_path, signal_key in _SELL_ADD_SIGNAL_TARGETS.values()
                    if target_path == allowed_path
                ),
                None,
            )
            if sell_target_key is None:
                skipped_patches.append(_apply_skipped(patch, "unsupported signal target path"))
                warnings.append(f"unsupported signal target path: {target_path}")
                continue

            if _get_path_value(applied_rules_preview, target_path) is not _MISSING:
                skipped_patches.append(_apply_skipped(patch, "target path already exists"))
                continue

            sell_section = applied_rules_preview.setdefault("sell", {})
            if not isinstance(sell_section, dict):
                skipped_patches.append(_apply_skipped(patch, "sell section is not a dict"))
                continue

            signals = sell_section.setdefault("signals", {})
            if not isinstance(signals, dict):
                skipped_patches.append(_apply_skipped(patch, "sell.signals is not a dict"))
                continue

            signal = deepcopy(_as_dict(patch.get("signal")))
            if not signal:
                skipped_patches.append(_apply_skipped(patch, "signal is not available"))
                continue
            signal.pop("preview_candidate", None)
            signal.pop("candidate_type", None)
            signal["enabled"] = signal.get("enabled") is True
            signals[sell_target_key] = signal
            applied_patches.append({
                "source_path": patch.get("source_path"),
                "target_path": target_path,
                "operation": operation,
                "added": True,
            })
            continue

        skipped_patches.append(_apply_skipped(patch, "unsupported patch operation"))
        warnings.append(f"unsupported patch operation: {operation}")

    return {
        "mode": "approved_rule_apply_preview",
        "stage": "RULE_APPLY_PREVIEW",
        "applied_rules_preview": applied_rules_preview,
        "applied_patches": applied_patches,
        "skipped_patches": skipped_patches,
        "summary": {
            "patches": len(patches),
            "applied": len(applied_patches),
            "skipped": len(skipped_patches),
        },
        "warnings": warnings,
    }


def _rule_commit_preview_diff_from_patch(patch: dict[str, Any]) -> list[dict[str, Any]]:
    operation = str(patch.get("operation") or "")
    target_path = str(patch.get("target_path") or "")
    diffs: list[dict[str, Any]] = []

    if operation == "set_value" and target_path == BAR_MINUTES_PATH:
        diffs.append({
            "path": BAR_MINUTES_PATH,
            "operation": "set_value",
            "change_type": "set_bar_minutes",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_signal_runtime_policy" and target_path == SIGNAL_RUNTIME_POLICY_PATH:
        diffs.append({
            "path": SIGNAL_RUNTIME_POLICY_PATH,
            "operation": "set_signal_runtime_policy",
            "change_type": "set_signal_runtime_policy",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_indicator" and target_path == RSI_INDICATOR_PATH:
        diffs.append({
            "path": RSI_INDICATOR_PATH,
            "operation": "set_indicator",
            "change_type": "set_rsi_indicator",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "merge_conditions" and target_path == BUY_CONDITIONS_PATH:
        for condition in _as_list(patch.get("add_conditions")):
            if not isinstance(condition, dict):
                continue
            diffs.append({
                "path": BUY_CONDITIONS_PATH,
                "operation": "merge_conditions",
                "change_type": "add_condition",
                "condition": deepcopy(condition),
                "preserved": [
                    "buy.groups",
                    "buy.groups[0].conditions existing OSC TURN_UP",
                ],
                "replace": False,
            })
        return diffs

    if operation == "set_filter" and target_path == BUY_MOVING_AVERAGE_FILTER_PATH:
        diffs.append({
            "path": BUY_MOVING_AVERAGE_FILTER_PATH,
            "operation": "set_filter",
            "change_type": "set_buy_current_price_ma60_filter",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_filter" and target_path == BUY_PRICE_COMPARE_FILTER_PATH:
        diffs.append({
            "path": BUY_PRICE_COMPARE_FILTER_PATH,
            "operation": "set_filter",
            "change_type": "set_buy_price_compare_filter",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_filter" and target_path == BUY_BOLLINGER_FILTER_PATH:
        diffs.append({
            "path": BUY_BOLLINGER_FILTER_PATH,
            "operation": "set_filter",
            "change_type": "set_buy_bollinger_filter",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_filter" and target_path == BUY_OCR_FILTER_PATH:
        diffs.append({
            "path": BUY_OCR_FILTER_PATH,
            "operation": "set_filter",
            "change_type": "set_buy_ocr_filter",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_filter" and target_path == BUY_RSI_FILTER_PATH:
        diffs.append({
            "path": BUY_RSI_FILTER_PATH,
            "operation": "set_filter",
            "change_type": "set_buy_rsi_filter",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_filter" and target_path == BUY_COMPOSITE_FILTER_PATH:
        diffs.append({
            "path": BUY_COMPOSITE_FILTER_PATH,
            "operation": "set_filter",
            "change_type": "set_buy_composite_filter",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_execution_policy" and target_path == BUY_EXECUTION_BASE_PATH:
        diffs.append({
            "path": BUY_EXECUTION_BASE_PATH,
            "operation": "set_execution_policy",
            "change_type": "set_buy_execution_base",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_execution_policy" and target_path == BUY_EXECUTION_REPEAT_PATH:
        diffs.append({
            "path": BUY_EXECUTION_REPEAT_PATH,
            "operation": "set_execution_policy",
            "change_type": "set_buy_execution_repeat",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_execution_policy" and target_path in {
        BUY_EXECUTION_ADDITIONAL_PATH,
        BUY_EXECUTION_CYCLE_PATH,
    }:
        diffs.append({
            "path": target_path,
            "operation": "set_execution_policy",
            "change_type": f"set_buy_execution_{target_path.rsplit('.', 1)[-1]}",
            "value": deepcopy(patch.get("value")),
            "replace": False,
        })
        return diffs

    if operation == "set_method_policy" and target_path in _SELL_METHOD_PATHS:
        diffs.append({
            "path": target_path,
            "operation": "set_method_policy",
            "change_type": "set_sell_method_policy",
            "value": deepcopy(patch.get("value")),
            "preserved": [
                "sell.signals",
                "runtime",
                "execution",
            ],
            "replace": False,
        })
        return diffs

    if operation == "set_signal" and target_path == SELL_PROFIT_RATE_SIGNAL_PATH:
        diffs.append({
            "path": SELL_PROFIT_RATE_SIGNAL_PATH,
            "operation": "set_signal",
            "change_type": "set_profit_rate_sell_signal",
            "value": deepcopy(patch.get("value")),
            "allowed_fields": sorted(_SELL_PROFIT_RATE_ALLOWED_FIELDS),
            "preserved": [
                "sell.signals.macd_sell",
                "sell.signals.ui_condition_a",
                "sell.signals.ui_condition_b",
                "sell.signals.ui_condition_c",
                "sell.signals.profit_rate_sell non-target fields",
            ],
            "replace": False,
        })
        return diffs

    if operation == "add_signal" and target_path in {target for target, _key in _SELL_ADD_SIGNAL_TARGETS.values()}:
        signal = _as_dict(patch.get("signal"))
        diffs.append({
            "path": target_path,
            "operation": "add_signal",
            "change_type": "add_disabled_signal",
            "enabled": False,
            "preserved": [
                "sell.signals.macd_sell",
            ],
            "replace": False,
        })
        return diffs

    return diffs


def build_rule_commit_preview(
    current_rules: dict[str, Any],
    preview_result: dict[str, Any],
    session: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a final preview of rule changes without saving or applying them."""
    current = deepcopy(current_rules) if isinstance(current_rules, dict) else {}
    preview = deepcopy(preview_result) if isinstance(preview_result, dict) else {}
    session_copy = deepcopy(session) if isinstance(session, dict) else {}
    context_copy = deepcopy(context) if isinstance(context, dict) else {}

    session_validation = validate_rule_approval_session_for_preview(session_copy, current, preview)
    decisions = _as_dict(session_copy.get("decisions"))
    approval_result = evaluate_rule_candidate_approval(preview, decisions)
    patch_preview = build_approved_rule_patch_preview(current, preview, approval_result)
    apply_preview = apply_approved_rule_patch_preview(current, patch_preview)
    apply_preview_hash = build_apply_preview_hash(apply_preview)

    final_diff: list[dict[str, Any]] = []
    for patch in _as_list(patch_preview.get("patches")):
        if isinstance(patch, dict):
            final_diff.extend(_rule_commit_preview_diff_from_patch(patch))

    blocked_reasons: list[str] = []
    warnings: list[str] = []
    warnings.extend(_as_list(session_validation.get("warnings")))
    warnings.extend(_as_list(approval_result.get("warnings")))
    warnings.extend(_as_list(patch_preview.get("warnings")))
    warnings.extend(_as_list(apply_preview.get("warnings")))

    if session_validation.get("valid") is not True:
        blocked_reasons.extend(_as_list(session_validation.get("blocked_reasons")))
        if not blocked_reasons:
            blocked_reasons.append("approval session validation must be VALID")
    if session_validation.get("path_match") is not True:
        blocked_reasons.append("approval session path_match must be true")
    if session_validation.get("type_match") is not True:
        blocked_reasons.append("approval session type_match must be true")
    if session_validation.get("fingerprint_match") is not True:
        blocked_reasons.append("approval session fingerprint_match must be true")
    if context_copy.get("approval_session_dirty") is True:
        blocked_reasons.append(
            "approval session has unsaved decision changes; save approval session before commit preview"
        )

    patches = _as_list(patch_preview.get("patches"))
    if not patches:
        blocked_reasons.append("approval session has no approved patches")

    skipped_patch_reasons = [
        str(skipped.get("reason"))
        for skipped in _as_list(apply_preview.get("skipped_patches"))
        if isinstance(skipped, dict)
    ]
    skipped_patch_reasons.extend(
        str(skipped.get("reason"))
        for skipped in _as_list(patch_preview.get("skipped_paths"))
        if isinstance(skipped, dict)
    )
    target_conflict = any("target path already exists" in reason for reason in skipped_patch_reasons)
    if target_conflict:
        blocked_reasons.append("target path conflict")

    buy_groups_replace = any(
        isinstance(diff, dict)
        and diff.get("path") == "buy.groups"
        and diff.get("replace") is True
        for diff in final_diff
    )
    macd_sell_replace = any(
        isinstance(diff, dict)
        and diff.get("path") == "sell.signals.macd_sell"
        and diff.get("replace") is True
        for diff in final_diff
    )
    if buy_groups_replace:
        blocked_reasons.append("buy.groups replace is not allowed")
    if macd_sell_replace:
        blocked_reasons.append("sell.signals.macd_sell replace is not allowed")

    commit_allowed = (
        not blocked_reasons
        and len(patches) > 0
        and len(_as_list(apply_preview.get("skipped_patches"))) == 0
        and not buy_groups_replace
        and not macd_sell_replace
    )

    return {
        "mode": "rule_commit_preview",
        "stage": "RULE_COMMIT_PREVIEW",
        "commit_allowed": commit_allowed,
        "blocked_reasons": blocked_reasons,
        "session_validation": session_validation,
        "approval_result": approval_result,
        "patch_preview": patch_preview,
        "apply_preview_summary": deepcopy(_as_dict(apply_preview.get("summary"))),
        "apply_preview_hash": apply_preview_hash,
        "apply_preview_hash_algorithm": "stable_json_sha256",
        "final_diff": final_diff,
        "safety_checks": {
            "rules_json_write": False,
            "engine_connected": False,
            "buy_groups_replace": buy_groups_replace,
            "macd_sell_replace": macd_sell_replace,
        },
        "warnings": warnings,
    }


def _commit_gate_blocked_reason_unique(reasons: list[str], reason: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)


def evaluate_rule_commit_gate_from_saved_session(
    current_rules: dict[str, Any],
    preview_result: dict[str, Any],
    session_path: Any,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the final pre-commit gate from a saved approval session only."""
    import rule_approval_session_file_service

    current = deepcopy(current_rules) if isinstance(current_rules, dict) else {}
    preview = deepcopy(preview_result) if isinstance(preview_result, dict) else {}
    context_copy = deepcopy(context) if isinstance(context, dict) else {}
    blocked_reasons: list[str] = []
    warnings: list[str] = []

    session_load = rule_approval_session_file_service.load_rule_approval_session(session_path)
    if session_load.get("exists") is not True:
        _commit_gate_blocked_reason_unique(blocked_reasons, "session file missing")
    if session_load.get("ok") is not True:
        for reason in _as_list(session_load.get("blocked_reasons")):
            _commit_gate_blocked_reason_unique(blocked_reasons, str(reason))
    warnings.extend(_as_list(session_load.get("warnings")))

    saved_session = session_load.get("session") if session_load.get("ok") is True else None
    if isinstance(saved_session, dict):
        session_restore = rule_approval_session_file_service.restore_saved_rule_approval_session(
            saved_session,
            current,
            preview,
        )
    else:
        session_restore = {
            "ok": False,
            "restore_status": "BLOCKED",
            "stage": "approval_session_restore_skipped",
            "session": None,
            "blocked_reasons": ["saved approval session is not available"],
            "warnings": [],
        }

    warnings.extend(_as_list(session_restore.get("warnings")))
    if session_restore.get("ok") is not True:
        for reason in _as_list(session_restore.get("blocked_reasons")):
            _commit_gate_blocked_reason_unique(blocked_reasons, str(reason))

    restored_session = session_restore.get("session") if session_restore.get("ok") is True else None
    restore_status = session_restore.get("restore_status")
    if restore_status != "RESTORED":
        _commit_gate_blocked_reason_unique(
            blocked_reasons,
            "saved approval session is stale; rerun validation and save approval session",
        )

    session_validation = validate_rule_approval_session_for_preview(
        restored_session if isinstance(restored_session, dict) else {},
        current,
        preview,
    )
    warnings.extend(_as_list(session_validation.get("warnings")))
    if session_validation.get("valid") is not True:
        for reason in _as_list(session_validation.get("blocked_reasons")):
            _commit_gate_blocked_reason_unique(blocked_reasons, str(reason))
        _commit_gate_blocked_reason_unique(
            blocked_reasons,
            "saved approval session is stale; rerun validation and save approval session",
        )

    current_rules_hash = _stable_hash(current)
    expected_rules_hash = context_copy.get("expected_rules_hash")
    rules_hash_match = isinstance(expected_rules_hash, str) and expected_rules_hash == current_rules_hash
    if not isinstance(expected_rules_hash, str) or not expected_rules_hash:
        _commit_gate_blocked_reason_unique(blocked_reasons, "expected rules hash is required")
    elif not rules_hash_match:
        _commit_gate_blocked_reason_unique(
            blocked_reasons,
            "rules changed after commit preview; rerun validation and commit preview",
        )

    approval_session_dirty = context_copy.get("approval_session_dirty") is True
    if approval_session_dirty:
        _commit_gate_blocked_reason_unique(
            blocked_reasons,
            "approval session has unsaved decision changes; save approval session before commit preview",
        )

    manual_confirmation = context_copy.get("manual_rule_commit_confirmed") is True
    if not manual_confirmation:
        _commit_gate_blocked_reason_unique(
            blocked_reasons,
            "manual rule commit confirmation is required",
        )

    commit_preview = build_rule_commit_preview(
        current,
        preview,
        restored_session if isinstance(restored_session, dict) else {},
        {"approval_session_dirty": approval_session_dirty},
    )
    warnings.extend(_as_list(commit_preview.get("warnings")))
    for reason in _as_list(commit_preview.get("blocked_reasons")):
        if reason in {
            "approval session has no approved patches",
            "target path conflict",
            "approval session has unsaved decision changes; save approval session before commit preview",
        }:
            _commit_gate_blocked_reason_unique(blocked_reasons, str(reason))
    if commit_preview.get("commit_allowed") is not True:
        _commit_gate_blocked_reason_unique(blocked_reasons, "commit preview is not allowed")
    if len(_as_list(commit_preview.get("final_diff"))) == 0:
        _commit_gate_blocked_reason_unique(blocked_reasons, "approval session has no approved patches")

    safety_checks = _as_dict(commit_preview.get("safety_checks"))
    for key in ("rules_json_write", "engine_connected", "buy_groups_replace", "macd_sell_replace"):
        if safety_checks.get(key) is not False:
            _commit_gate_blocked_reason_unique(blocked_reasons, f"unsafe commit preview safety check: {key}")

    apply_preview_hash = commit_preview.get("apply_preview_hash")
    apply_preview_hash_algorithm = commit_preview.get("apply_preview_hash_algorithm")
    if not isinstance(apply_preview_hash, str) or not apply_preview_hash:
        _commit_gate_blocked_reason_unique(blocked_reasons, "apply preview hash is required")
    if apply_preview_hash_algorithm != "stable_json_sha256":
        _commit_gate_blocked_reason_unique(blocked_reasons, "apply preview hash algorithm is invalid")

    commit_allowed = (
        not blocked_reasons
        and session_load.get("exists") is True
        and session_load.get("ok") is True
        and restore_status == "RESTORED"
        and session_validation.get("valid") is True
        and session_validation.get("path_match") is True
        and session_validation.get("type_match") is True
        and session_validation.get("fingerprint_match") is True
        and not approval_session_dirty
        and rules_hash_match
        and commit_preview.get("commit_allowed") is True
        and len(_as_list(commit_preview.get("final_diff"))) > 0
        and manual_confirmation
        and all(safety_checks.get(key) is False for key in (
            "rules_json_write",
            "engine_connected",
            "buy_groups_replace",
            "macd_sell_replace",
        ))
    )

    return {
        "mode": "rule_commit_gate",
        "routine": "지표추종매매",
        "routine_key": "indicator_follow",
        "stage": "RULE_COMMIT_GATE",
        "commit_allowed": commit_allowed,
        "blocked_reasons": blocked_reasons,
        "session_load": session_load,
        "session_restore": session_restore,
        "session_validation": session_validation,
        "commit_preview": commit_preview,
        "apply_preview_hash": apply_preview_hash,
        "apply_preview_hash_algorithm": apply_preview_hash_algorithm,
        "rules_hash_check": {
            "expected_rules_hash": expected_rules_hash,
            "current_rules_hash": current_rules_hash,
            "match": rules_hash_match,
        },
        "manual_confirmation": manual_confirmation,
        "warnings": warnings,
    }


def _approval_path_set(approvals: Any) -> set[str]:
    if isinstance(approvals, dict):
        for key in ("approved_paths", "paths"):
            value = approvals.get(key)
            if isinstance(value, (list, tuple, set)):
                return {str(path) for path in value}
        return {str(path) for path, approved in approvals.items() if approved is True}
    if isinstance(approvals, (list, tuple, set)):
        return {str(path) for path in approvals}
    return set()


def approve_engine_rule_candidates(
    current_rules: dict[str, Any],
    preview_result: dict[str, Any],
    approvals: Any,
) -> dict[str, Any]:
    """Return a copied rules dict with approved preview candidates applied."""
    approved_rules = deepcopy(current_rules) if isinstance(current_rules, dict) else {}
    approved_paths = _approval_path_set(approvals)
    preview = _as_dict(preview_result)
    preview_rules = _as_dict(preview.get("preview_rules"))
    preview_candidates = _as_dict(
        _as_dict(preview_rules.get("indicator_follow_rule_preview")).get("candidates")
    )
    known_paths = {
        BAR_MINUTES_PATH,
        BUY_CONDITIONS_PATH,
        BUY_MOVING_AVERAGE_FILTER_PATH,
        BUY_PRICE_COMPARE_FILTER_PATH,
        BUY_BOLLINGER_FILTER_PATH,
        BUY_OCR_FILTER_PATH,
        BUY_RSI_FILTER_PATH,
        BUY_COMPOSITE_FILTER_PATH,
        BUY_EXECUTION_BASE_PATH,
        BUY_EXECUTION_REPEAT_PATH,
        BUY_EXECUTION_ADDITIONAL_PATH,
        BUY_EXECUTION_CYCLE_PATH,
        SELL_METHOD_SELECTED_SETS_PATH,
        SELL_METHOD_SETTING_A_PATH,
        SELL_METHOD_SETTING_B_PATH,
        SELL_METHOD_SETTING_C_PATH,
        RSI_INDICATOR_PATH,
        SELL_PROFIT_RATE_SIGNAL_PATH,
        SELL_CONDITION_A_SIGNAL_PREVIEW_PATH,
        SELL_CONDITION_B_SIGNAL_PREVIEW_PATH,
        SELL_CONDITION_C_SIGNAL_PREVIEW_PATH,
        SELL_MACD_SIGNAL_PREVIEW_PATH,
    }
    applied_paths: list[str] = []
    skipped_paths: list[str] = []
    warnings: list[str] = []

    for path in sorted(approved_paths - known_paths):
        skipped_paths.append(path)
        warnings.append(f"unknown approval path skipped: {path}")

    if BAR_MINUTES_PATH in approved_paths:
        bar_candidate = _as_dict(preview_candidates.get("bar"))
        if "value" not in bar_candidate:
            skipped_paths.append(BAR_MINUTES_PATH)
            warnings.append("bar approval skipped: value is not available")
        else:
            bar_section = approved_rules.setdefault("bar", {})
            if not isinstance(bar_section, dict):
                skipped_paths.append(BAR_MINUTES_PATH)
                warnings.append("bar approval skipped: bar section is not a dict")
            else:
                bar_section["bar_minutes"] = deepcopy(bar_candidate.get("value"))
                applied_paths.append(BAR_MINUTES_PATH)

    if RSI_INDICATOR_PATH in approved_paths:
        rsi_candidate = _as_dict(_as_dict(preview_candidates.get("indicators")).get("rsi"))
        candidate_value = rsi_candidate.get("value")
        if not isinstance(candidate_value, dict):
            skipped_paths.append(RSI_INDICATOR_PATH)
            warnings.append("RSI approval skipped: value is not available")
        else:
            indicators_section = approved_rules.setdefault("indicators", {})
            if not isinstance(indicators_section, dict):
                skipped_paths.append(RSI_INDICATOR_PATH)
                warnings.append("RSI approval skipped: indicators section is not a dict")
            else:
                indicators_section["rsi"] = deepcopy(candidate_value)
                applied_paths.append(RSI_INDICATOR_PATH)

    if BUY_CONDITIONS_PATH in approved_paths:
        buy_candidate = _as_dict(preview_candidates.get("buy"))
        add_conditions = buy_candidate.get("add_conditions")
        buy_section = _as_dict(approved_rules.get("buy"))
        groups = buy_section.get("groups")
        if not isinstance(groups, list) or not groups or not isinstance(groups[0], dict):
            skipped_paths.append(BUY_CONDITIONS_PATH)
            warnings.append("buy approval skipped: buy.groups[0] is not available")
        else:
            conditions = groups[0].get("conditions")
            if not isinstance(conditions, list):
                skipped_paths.append(BUY_CONDITIONS_PATH)
                warnings.append("buy approval skipped: buy.groups[0].conditions is not a list")
            elif not isinstance(add_conditions, list):
                skipped_paths.append(BUY_CONDITIONS_PATH)
                warnings.append("buy approval skipped: add_conditions is not a list")
            else:
                added_count = 0
                for condition in add_conditions:
                    if not isinstance(condition, dict):
                        continue
                    if any(isinstance(existing, dict) and _condition_matches(existing, condition) for existing in conditions):
                        continue
                    conditions.append(deepcopy(condition))
                    added_count += 1
                applied_paths.append(BUY_CONDITIONS_PATH)
                if added_count == 0:
                    warnings.append("buy approval applied with no new conditions")

    if BUY_MOVING_AVERAGE_FILTER_PATH in approved_paths:
        filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("moving_average"))
        candidate_value = _moving_average_filter_value(filter_candidate)
        if not candidate_value:
            skipped_paths.append(BUY_MOVING_AVERAGE_FILTER_PATH)
            warnings.append("BUY moving_average filter approval skipped: value is not available")
        elif _set_path_value(approved_rules, BUY_MOVING_AVERAGE_FILTER_PATH, candidate_value):
            applied_paths.append(BUY_MOVING_AVERAGE_FILTER_PATH)
        else:
            skipped_paths.append(BUY_MOVING_AVERAGE_FILTER_PATH)
            warnings.append("BUY moving_average filter approval skipped: target path is not writable")

    if BUY_PRICE_COMPARE_FILTER_PATH in approved_paths:
        filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("price_compare"))
        candidate_value = _price_compare_filter_value(filter_candidate)
        if not candidate_value:
            skipped_paths.append(BUY_PRICE_COMPARE_FILTER_PATH)
            warnings.append("BUY price_compare filter approval skipped: value is not available")
        elif _set_path_value(approved_rules, BUY_PRICE_COMPARE_FILTER_PATH, candidate_value):
            applied_paths.append(BUY_PRICE_COMPARE_FILTER_PATH)
        else:
            skipped_paths.append(BUY_PRICE_COMPARE_FILTER_PATH)
            warnings.append("BUY price_compare filter approval skipped: target path is not writable")

    if BUY_BOLLINGER_FILTER_PATH in approved_paths:
        filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("bollinger"))
        candidate_value = _bollinger_filter_value(filter_candidate)
        if not candidate_value:
            skipped_paths.append(BUY_BOLLINGER_FILTER_PATH)
            warnings.append("BUY bollinger filter approval skipped: value is not available")
        elif _set_path_value(approved_rules, BUY_BOLLINGER_FILTER_PATH, candidate_value):
            applied_paths.append(BUY_BOLLINGER_FILTER_PATH)
        else:
            skipped_paths.append(BUY_BOLLINGER_FILTER_PATH)
            warnings.append("BUY bollinger filter approval skipped: target path is not writable")

    if BUY_OCR_FILTER_PATH in approved_paths:
        filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("ocr"))
        candidate_value = _ocr_filter_value(filter_candidate)
        if not candidate_value:
            skipped_paths.append(BUY_OCR_FILTER_PATH)
            warnings.append("BUY ocr filter approval skipped: value is not available")
        elif _set_path_value(approved_rules, BUY_OCR_FILTER_PATH, candidate_value):
            applied_paths.append(BUY_OCR_FILTER_PATH)
        else:
            skipped_paths.append(BUY_OCR_FILTER_PATH)
            warnings.append("BUY ocr filter approval skipped: target path is not writable")

    if BUY_RSI_FILTER_PATH in approved_paths:
        filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("rsi"))
        candidate_value = _rsi_filter_value(filter_candidate)
        if not candidate_value:
            skipped_paths.append(BUY_RSI_FILTER_PATH)
            warnings.append("BUY rsi filter approval skipped: value is not available")
        elif _set_path_value(approved_rules, BUY_RSI_FILTER_PATH, candidate_value):
            applied_paths.append(BUY_RSI_FILTER_PATH)
        else:
            skipped_paths.append(BUY_RSI_FILTER_PATH)
            warnings.append("BUY rsi filter approval skipped: target path is not writable")

    if BUY_COMPOSITE_FILTER_PATH in approved_paths:
        filter_candidate = _as_dict(_as_dict(preview_candidates.get("filters")).get("composite"))
        candidate_value = _composite_filter_value(filter_candidate)
        if not candidate_value:
            skipped_paths.append(BUY_COMPOSITE_FILTER_PATH)
            warnings.append("BUY composite filter approval skipped: value is not available")
        elif _set_path_value(approved_rules, BUY_COMPOSITE_FILTER_PATH, candidate_value):
            applied_paths.append(BUY_COMPOSITE_FILTER_PATH)
        else:
            skipped_paths.append(BUY_COMPOSITE_FILTER_PATH)
            warnings.append("BUY composite filter approval skipped: target path is not writable")

    if BUY_EXECUTION_BASE_PATH in approved_paths:
        execution_candidate = _as_dict(_as_dict(preview_candidates.get("execution")).get("base"))
        candidate_value = _execution_policy_value(execution_candidate)
        if _execution_candidate_is_locked(execution_candidate):
            skipped_paths.append(BUY_EXECUTION_BASE_PATH)
            warnings.append(
                "BUY execution base approval blocked: "
                f"{execution_candidate.get('execution_lock_reason') or P1_EXECUTION_LOCK_REASON}"
            )
        elif not candidate_value:
            skipped_paths.append(BUY_EXECUTION_BASE_PATH)
            warnings.append("BUY execution base approval skipped: value is not available")
        elif _set_buy_execution_policy_value(
            approved_rules,
            BUY_EXECUTION_BASE_PATH,
            candidate_value,
        ):
            applied_paths.append(BUY_EXECUTION_BASE_PATH)
        else:
            skipped_paths.append(BUY_EXECUTION_BASE_PATH)
            warnings.append("BUY execution base approval skipped: target path is not writable")

    if BUY_EXECUTION_REPEAT_PATH in approved_paths:
        execution_candidate = _as_dict(_as_dict(preview_candidates.get("execution")).get("repeat"))
        candidate_value = _execution_policy_value(execution_candidate)
        if not candidate_value:
            skipped_paths.append(BUY_EXECUTION_REPEAT_PATH)
            warnings.append("BUY execution repeat approval skipped: value is not available")
        elif _set_buy_execution_policy_value(
            approved_rules,
            BUY_EXECUTION_REPEAT_PATH,
            candidate_value,
        ):
            applied_paths.append(BUY_EXECUTION_REPEAT_PATH)
        else:
            skipped_paths.append(BUY_EXECUTION_REPEAT_PATH)
            warnings.append("BUY execution repeat approval skipped: target path is not writable")

    for execution_path in (BUY_EXECUTION_ADDITIONAL_PATH, BUY_EXECUTION_CYCLE_PATH):
        if execution_path not in approved_paths:
            continue
        candidate_key = _BUY_EXECUTION_CANDIDATE_KEYS[execution_path]
        execution_candidate = _as_dict(
            _as_dict(preview_candidates.get("execution")).get(candidate_key)
        )
        candidate_value = _execution_policy_value(execution_candidate)
        if _execution_candidate_is_locked(execution_candidate):
            skipped_paths.append(execution_path)
            warnings.append(
                f"BUY execution {candidate_key} approval blocked: "
                f"{execution_candidate.get('execution_lock_reason') or P1_EXECUTION_LOCK_REASON}"
            )
        elif not candidate_value:
            skipped_paths.append(execution_path)
            warnings.append(f"BUY execution {candidate_key} approval skipped: value is not available")
        elif _set_buy_execution_policy_value(approved_rules, execution_path, candidate_value):
            applied_paths.append(execution_path)
        else:
            skipped_paths.append(execution_path)
            warnings.append(f"BUY execution {candidate_key} approval skipped: target path is not writable")

    method_policy_candidates = _as_dict(_as_dict(preview_candidates.get("sell")).get("method_policy_candidates"))
    for method_path in (
        SELL_METHOD_SELECTED_SETS_PATH,
        SELL_METHOD_SETTING_A_PATH,
        SELL_METHOD_SETTING_B_PATH,
        SELL_METHOD_SETTING_C_PATH,
    ):
        if method_path not in approved_paths:
            continue
        method_candidate = _as_dict(method_policy_candidates.get(method_path))
        candidate_value = _method_policy_value(method_candidate)
        if candidate_value is _MISSING:
            skipped_paths.append(method_path)
            warnings.append("SELL method policy approval skipped: value is not available")
        elif _set_path_value(approved_rules, method_path, candidate_value):
            applied_paths.append(method_path)
        else:
            skipped_paths.append(method_path)
            warnings.append("SELL method policy approval skipped: target path is not writable")

    if SELL_PROFIT_RATE_SIGNAL_PATH in approved_paths:
        signal_candidate = _sell_set_signal_candidate(preview_candidates, SELL_PROFIT_RATE_SIGNAL_PATH)
        candidate_value = _profit_rate_signal_value(signal_candidate)
        if not candidate_value:
            skipped_paths.append(SELL_PROFIT_RATE_SIGNAL_PATH)
            warnings.append("profit_rate_sell approval skipped: value is not available")
        else:
            sell_section = approved_rules.setdefault("sell", {})
            if not isinstance(sell_section, dict):
                skipped_paths.append(SELL_PROFIT_RATE_SIGNAL_PATH)
                warnings.append("profit_rate_sell approval skipped: sell section is not a dict")
            else:
                signals = sell_section.setdefault("signals", {})
                if not isinstance(signals, dict):
                    skipped_paths.append(SELL_PROFIT_RATE_SIGNAL_PATH)
                    warnings.append("profit_rate_sell approval skipped: sell.signals is not a dict")
                else:
                    profit_signal = signals.setdefault("profit_rate_sell", {})
                    if not isinstance(profit_signal, dict):
                        skipped_paths.append(SELL_PROFIT_RATE_SIGNAL_PATH)
                        warnings.append("profit_rate_sell approval skipped: signal is not a dict")
                    else:
                        for key in ("enabled", "profit_rate_percent", "basis"):
                            if key in candidate_value:
                                profit_signal[key] = deepcopy(candidate_value[key])
                        applied_paths.append(SELL_PROFIT_RATE_SIGNAL_PATH)

    for sell_preview_path in (
        SELL_CONDITION_A_SIGNAL_PREVIEW_PATH,
        SELL_CONDITION_B_SIGNAL_PREVIEW_PATH,
        SELL_CONDITION_C_SIGNAL_PREVIEW_PATH,
        SELL_MACD_SIGNAL_PREVIEW_PATH,
    ):
        if sell_preview_path not in approved_paths:
            continue
        sell_target = _sell_add_signal_target(sell_preview_path)
        if sell_target is None:
            continue
        _target_path, target_key = sell_target
        sell_candidate = _sell_add_signal_candidate(preview_candidates, sell_preview_path)
        sell_section = approved_rules.setdefault("sell", {})
        if not isinstance(sell_section, dict):
            skipped_paths.append(sell_preview_path)
            warnings.append("sell approval skipped: sell section is not a dict")
        else:
            signals = sell_section.setdefault("signals", {})
            if not isinstance(signals, dict):
                skipped_paths.append(sell_preview_path)
                warnings.append("sell approval skipped: sell.signals is not a dict")
            elif not sell_candidate:
                skipped_paths.append(sell_preview_path)
                warnings.append("sell approval skipped: add_signal_candidate is not available")
            else:
                approved_signal = _sell_add_signal_payload(sell_candidate)
                approved_signal.pop("path", None)
                approved_signal.pop("candidate_type", None)
                approved_signal.pop("preview_candidate", None)
                approved_signal["enabled"] = approved_signal.get("enabled") is True
                signals[target_key] = approved_signal
                applied_paths.append(sell_preview_path)

    return {
        "rules": approved_rules,
        "applied_paths": applied_paths,
        "skipped_paths": skipped_paths,
        "warnings": warnings,
    }


def compare_engine_rules_preview(
    current_rules: dict[str, Any],
    preview_result: dict[str, Any],
) -> dict[str, Any]:
    """Compare current rules with a preview result by mapped paths only."""
    current = _as_dict(current_rules)
    preview = _as_dict(preview_result)
    preview_rules = _as_dict(preview.get("preview_rules"))
    mapped_paths = preview.get("mapped_paths")
    paths = mapped_paths if isinstance(mapped_paths, list) else []
    validation_warnings = preview.get("validation_warnings")
    postponed = preview.get("postponed")
    legacy_notices = preview.get("legacy_notices")
    execution_locks = preview.get("execution_locks")
    fallback_warnings = preview.get("warnings")
    validation_warning_list = validation_warnings if isinstance(validation_warnings, list) else []
    postponed_list = postponed if isinstance(postponed, list) else []
    legacy_notice_list = legacy_notices if isinstance(legacy_notices, list) else []
    execution_lock_list = execution_locks if isinstance(execution_locks, list) else []
    if not validation_warning_list and not postponed_list and not execution_lock_list \
            and isinstance(fallback_warnings, list):
        validation_warning_list = fallback_warnings
    warning_list = list(validation_warning_list) + list(postponed_list) + list(execution_lock_list)
    preview_candidates = _as_dict(
        _as_dict(preview_rules.get("indicator_follow_rule_preview")).get("candidates")
    )

    summary = {
        "same": 0,
        "changed": 0,
        "added": 0,
        "missing": 0,
        "merge_candidate": 0,
        "add_signal_candidate": 0,
        "validation": len(validation_warning_list),
        "postponed": len(postponed_list),
        "legacy": len(legacy_notice_list),
        "execution_locks": len(execution_lock_list),
        "warnings_total": len(warning_list),
    }
    changes: list[dict[str, Any]] = []

    for path in paths:
        if not isinstance(path, str):
            continue

        current_value = _get_path_value(current, path)
        if path == BUY_CONDITIONS_PATH:
            preview_value = _as_dict(preview_candidates.get("buy"))
        elif path == BUY_MOVING_AVERAGE_FILTER_PATH:
            preview_value = _moving_average_filter_value(
                _as_dict(_as_dict(preview_candidates.get("filters")).get("moving_average"))
            )
        elif path == BUY_PRICE_COMPARE_FILTER_PATH:
            preview_value = _price_compare_filter_value(
                _as_dict(_as_dict(preview_candidates.get("filters")).get("price_compare"))
            )
        elif path == BUY_BOLLINGER_FILTER_PATH:
            preview_value = _bollinger_filter_value(
                _as_dict(_as_dict(preview_candidates.get("filters")).get("bollinger"))
            )
        elif path == BUY_OCR_FILTER_PATH:
            preview_value = _ocr_filter_value(
                _as_dict(_as_dict(preview_candidates.get("filters")).get("ocr"))
            )
        elif path == BUY_RSI_FILTER_PATH:
            preview_value = _rsi_filter_value(
                _as_dict(_as_dict(preview_candidates.get("filters")).get("rsi"))
            )
        elif path == BUY_COMPOSITE_FILTER_PATH:
            preview_value = _composite_filter_value(
                _as_dict(_as_dict(preview_candidates.get("filters")).get("composite"))
            )
        elif path == BUY_EXECUTION_BASE_PATH:
            preview_value = _execution_policy_value(
                _as_dict(_as_dict(preview_candidates.get("execution")).get("base"))
            )
        elif path == BUY_EXECUTION_REPEAT_PATH:
            preview_value = _execution_policy_value(
                _as_dict(_as_dict(preview_candidates.get("execution")).get("repeat"))
            )
        elif path in {BUY_EXECUTION_ADDITIONAL_PATH, BUY_EXECUTION_CYCLE_PATH}:
            preview_value = _execution_policy_value(
                _as_dict(
                    _as_dict(preview_candidates.get("execution")).get(
                        _BUY_EXECUTION_CANDIDATE_KEYS[path]
                    )
                )
            )
        elif path in _SELL_METHOD_PATHS:
            preview_value = _method_policy_value(
                _as_dict(_as_dict(_as_dict(preview_candidates.get("sell")).get("method_policy_candidates")).get(path))
            )
        elif path == RSI_INDICATOR_PATH:
            preview_value = _as_dict(_as_dict(preview_candidates.get("indicators")).get("rsi")).get("value", _MISSING)
        elif path == SELL_PROFIT_RATE_SIGNAL_PATH:
            preview_value = _profit_rate_signal_value(
                _sell_set_signal_candidate(preview_candidates, path)
            )
        elif path in _SELL_ADD_SIGNAL_TARGETS:
            preview_value = _sell_add_signal_candidate(preview_candidates, path)
        else:
            preview_value = _get_path_value(preview_rules, path)
        current_exists = current_value is not _MISSING
        preview_exists = preview_value is not _MISSING and preview_value != {}

        if path == BUY_CONDITIONS_PATH and preview_exists:
            status = "merge_candidate"
        elif path == BUY_MOVING_AVERAGE_FILTER_PATH and preview_exists:
            status = "changed" if current_exists else "added"
        elif path == BUY_PRICE_COMPARE_FILTER_PATH and preview_exists:
            status = "changed" if current_exists else "added"
        elif path == BUY_OCR_FILTER_PATH and preview_exists:
            status = "changed" if current_exists else "added"
        elif path == BUY_RSI_FILTER_PATH and preview_exists:
            status = "changed" if current_exists else "added"
        elif path == BUY_COMPOSITE_FILTER_PATH and preview_exists:
            status = "changed" if current_exists else "added"
        elif path in _BUY_EXECUTION_PATHS and preview_exists:
            status = "changed" if current_exists else "added"
        elif path in _SELL_METHOD_PATHS and preview_exists:
            status = "changed" if current_exists else "added"
        elif path == SELL_PROFIT_RATE_SIGNAL_PATH and preview_exists:
            status = "changed" if current_exists else "added"
        elif path in _SELL_ADD_SIGNAL_TARGETS and preview_exists:
            status = "add_signal_candidate"
        elif current_exists and preview_exists:
            status = "same" if current_value == preview_value else "changed"
        elif not current_exists and preview_exists:
            status = "added"
        else:
            status = "missing"

        summary[status] += 1
        changes.append({
            "path": path,
            "status": status,
            "current_value": current_value if current_exists else None,
            "preview_value": preview_value if preview_exists else None,
            "risk": _preview_diff_risk(path),
            "note": _preview_diff_note(path),
        })

    return {
        "changes": changes,
        "summary": summary,
        "validation_warnings": list(validation_warning_list),
        "postponed": list(postponed_list),
        "legacy_notices": list(legacy_notice_list),
        "execution_locks": list(execution_lock_list),
        "warnings": list(warning_list),
    }
