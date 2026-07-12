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
from typing import Any


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
BUY_CONDITIONS_PATH = "buy.groups[0].conditions"
BUY_MOVING_AVERAGE_FILTER_PATH = "buy.filters.moving_average"
BUY_PRICE_COMPARE_FILTER_PATH = "buy.filters.price_compare"
BUY_BOLLINGER_FILTER_PATH = "buy.filters.bollinger"
BUY_OCR_FILTER_PATH = "buy.filters.ocr"
BUY_RSI_FILTER_PATH = "buy.filters.rsi"
BUY_COMPOSITE_FILTER_PATH = "buy.filters.composite"
BUY_EXECUTION_BASE_PATH = "buy.execution.base"
BUY_EXECUTION_REPEAT_PATH = "buy.execution.repeat"
RSI_INDICATOR_PATH = "indicators.rsi"
SELL_MACD_SIGNAL_PREVIEW_PATH = "sell.signals.ui_preview_condition_c_macd_sell"
APPROVED_SELL_MACD_SIGNAL_KEY = "ui_condition_c_macd_sell"
SELL_MACD_SIGNAL_TARGET_PATH = f"sell.signals.{APPROVED_SELL_MACD_SIGNAL_KEY}"
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


def _preview_diff_risk(path: str) -> str:
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
    if path in {BUY_EXECUTION_BASE_PATH, BUY_EXECUTION_REPEAT_PATH}:
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
        "sell.signals.macd_sell": (
            "UI preview-only sell MACD condition candidate; does not replace existing rules."
        ),
        SELL_MACD_SIGNAL_PREVIEW_PATH: (
            "UI preview-only add signal candidate; existing sell.signals.macd_sell is unchanged."
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
        "hoga_mode": _hoga_mode_token(base.get("hoga_combo")),
        "order_price_basis": _price_basis_token(base.get("order_combo")),
        "hoga_up": _safe_int(base.get("up_line")),
        "hoga_down": _safe_int(base.get("down_line")),
        "point_mode": _point_mode_token(base.get("time_mode_combo")),
        "point_value": _safe_float(base.get("time_value_line")),
        "point_unit": _point_unit_token(base.get("time_unit_combo")),
        "point_range": _range_token(base.get("time_range_combo")),
        "point_count": _safe_int(base.get("time_count_line")),
        "ratio_left": _price_basis_token(base.get("ratio_left_combo") or base.get("time_order_combo")),
        "ratio_right": _price_basis_token(base.get("ratio_right_combo")),
        "ratio_direction": _direction_token(base.get("ratio_direction_combo")),
        "ratio_value": _safe_float(base.get("ratio_value_line")),
        "ratio_compare": _ratio_compare_token(base.get("ratio_compare_combo")),
        "ratio_count": _safe_int(base.get("ratio_count_line")),
    }
    return {
        "path": BUY_EXECUTION_BASE_PATH,
        "operation": "set_execution_policy",
        "value": value,
    }


def _build_buy_execution_repeat_candidate(repeat: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if not _truthy_ui(repeat.get("apply_all_check")):
        return None

    value = {
        "apply_all": True,
        "detail_mode": _detail_mode_token(repeat.get("detail_mode_combo")),
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
        if below_operator is None:
            warnings.append(f"buy price compare condition is not mapped: {price_compare.get('condition_combo')!r}")
            return None
        if above_operator is None:
            warnings.append(f"buy price compare above condition is not mapped: {price_compare.get('above_condition_combo')!r}")
            return None
        conditions.append(_price_compare_condition(
            target="AVG_PRICE",
            operator=below_operator,
            compare_target="ORDER_PRICE",
            description="UI preview: BUY price compare below-branch filter condition",
        ))
        conditions.append(_price_compare_condition(
            target="AVG_PRICE",
            operator=above_operator,
            compare_target="ORDER_PRICE",
            description="UI preview: BUY price compare above-branch filter condition",
        ))

    return {
        "path": BUY_PRICE_COMPARE_FILTER_PATH,
        "value": {
            "enabled": True,
            "conditions_logic": "OR",
            "conditions": conditions,
        },
    }


def _build_sell_condition_c_indicator_condition(condition_c: dict[str, Any], warnings: list[str]) -> dict[str, Any] | None:
    if condition_c.get("macd_check") is False:
        warnings.append("sell condition C MACD row is unchecked")
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

    buy_ui = _as_dict(state.get("buy_ui"))
    signal_filter = _as_dict(buy_ui.get("signal_filter"))
    price_compare = _as_dict(buy_ui.get("price_compare"))
    execution_base = _as_dict(buy_ui.get("base"))
    execution_repeat = _as_dict(buy_ui.get("repeat"))

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
    if buy_execution_base_candidate:
        _set_path_value(preview_rules, BUY_EXECUTION_BASE_PATH, buy_execution_base_candidate["value"])
        preview_candidates.setdefault("execution", {})["base"] = buy_execution_base_candidate

    buy_execution_repeat_candidate = _build_buy_execution_repeat_candidate(execution_repeat, validation_warnings)
    if buy_execution_repeat_candidate:
        _set_path_value(preview_rules, BUY_EXECUTION_REPEAT_PATH, buy_execution_repeat_candidate["value"])
        preview_candidates.setdefault("execution", {})["repeat"] = buy_execution_repeat_candidate

    rsi_candidate = _build_rsi_indicator_candidate(source_rules, signal_filter, validation_warnings)
    if rsi_candidate:
        indicators_section = preview_rules.setdefault("indicators", {})
        if isinstance(indicators_section, dict):
            indicators_section["rsi"] = deepcopy(rsi_candidate["value"])
        preview_candidates["indicators"] = {
            "rsi": rsi_candidate,
        }

    sell_ui = _as_dict(state.get("sell_ui"))
    signal_conditions = _as_dict(sell_ui.get("signal_conditions"))
    condition_c = _as_dict(signal_conditions.get("condition_c"))
    sell_indicator_condition = _build_sell_condition_c_indicator_condition(condition_c, validation_warnings)
    if sell_indicator_condition:
        preview_candidates["sell"] = {
            "add_signal_candidate": {
                "path": SELL_MACD_SIGNAL_PREVIEW_PATH,
                "enabled": False,
                "preview_candidate": True,
                "groups_logic": "OR",
                "groups": [{
                    "enabled": True,
                    "name": "UI_PREVIEW_SELL_MACD_CONDITION_C",
                    "conditions_logic": "AND",
                    "conditions": [sell_indicator_condition],
                }],
            }
        }
        legacy_notices.append("sell condition C MACD is an add_signal_candidate and does not replace existing macd_sell")
    else:
        validation_warnings.append("sell condition C MACD candidate group was not generated")

    preview_rules["indicator_follow_rule_preview"] = {
        "mode": "merge_add_candidate",
        "candidates": preview_candidates,
    }

    if not buy_execution_base_candidate:
        postponed.append("buy method mapping is postponed")
    if not buy_execution_repeat_candidate:
        postponed.append("repeat buy mapping is postponed")
    postponed.extend([
        "situation response mapping is postponed",
        "additional feature mapping is postponed",
        "cycle setting mapping is postponed",
        "exit condition mapping is postponed",
        "sell method A/B/C mapping is postponed",
        "pending order policy mapping is postponed",
        "completion policy mapping is postponed",
    ])

    mapped_paths = [
        BAR_MINUTES_PATH,
    ]
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
    mapped_paths.extend([
        RSI_INDICATOR_PATH,
        SELL_MACD_SIGNAL_PREVIEW_PATH,
    ])

    warnings = list(validation_warnings) + list(postponed)
    return {
        "preview_rules": preview_rules,
        "mapped_paths": mapped_paths,
        "validation_warnings": validation_warnings,
        "postponed": postponed,
        "legacy_notices": legacy_notices,
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
        "warnings": list(preview_result.get("warnings", [])),
    }
    return {
        "pending_rules": pending_rules,
        "preview_result": preview_result,
        "validation_warnings": list(preview_result.get("validation_warnings", [])),
        "postponed": list(preview_result.get("postponed", [])),
        "legacy_notices": list(preview_result.get("legacy_notices", [])),
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

    rsi_candidate = _as_dict(_as_dict(candidates.get("indicators")).get("rsi"))
    if rsi_candidate:
        rsi_path = str(rsi_candidate.get("path") or RSI_INDICATOR_PATH)
        candidate_paths[rsi_path] = "set_indicator"

    sell_candidate = _as_dict(_as_dict(candidates.get("sell")).get("add_signal_candidate"))
    if sell_candidate:
        signal_path = str(sell_candidate.get("path") or SELL_MACD_SIGNAL_PREVIEW_PATH)
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
        BUY_CONDITIONS_PATH: _get_path_value(rules, BUY_CONDITIONS_PATH),
        BUY_MOVING_AVERAGE_FILTER_PATH: _get_path_value(rules, BUY_MOVING_AVERAGE_FILTER_PATH),
        BUY_BOLLINGER_FILTER_PATH: _get_path_value(rules, BUY_BOLLINGER_FILTER_PATH),
        BUY_OCR_FILTER_PATH: _get_path_value(rules, BUY_OCR_FILTER_PATH),
        BUY_RSI_FILTER_PATH: _get_path_value(rules, BUY_RSI_FILTER_PATH),
        BUY_COMPOSITE_FILTER_PATH: _get_path_value(rules, BUY_COMPOSITE_FILTER_PATH),
        BUY_PRICE_COMPARE_FILTER_PATH: _get_path_value(rules, BUY_PRICE_COMPARE_FILTER_PATH),
        BUY_EXECUTION_BASE_PATH: _get_path_value(rules, BUY_EXECUTION_BASE_PATH),
        BUY_EXECUTION_REPEAT_PATH: _get_path_value(rules, BUY_EXECUTION_REPEAT_PATH),
        RSI_INDICATOR_PATH: _get_path_value(rules, RSI_INDICATOR_PATH),
        "sell.signals.macd_sell": _get_path_value(rules, "sell.signals.macd_sell"),
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
    fingerprint_payload = {
        "candidate_hash": candidate_hash,
        "current_rule_target_hash": current_rule_target_hash,
    }
    return {
        "mode": "approval_candidate_fingerprint",
        "preview_mode": preview_namespace.get("mode"),
        "candidate_paths": candidate_path_list,
        "candidate_types": candidate_paths,
        "candidate_hash": candidate_hash,
        "current_rule_target_hash": current_rule_target_hash,
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

        if path == SELL_MACD_SIGNAL_PREVIEW_PATH:
            sell_candidate = _as_dict(_as_dict(preview_candidates.get("sell")).get("add_signal_candidate"))
            if not sell_candidate:
                skipped_paths.append(_patch_skipped(path, "sell add_signal_candidate is not available"))
                continue

            signal = deepcopy(sell_candidate)
            signal.pop("path", None)
            signal.pop("preview_candidate", None)
            signal["enabled"] = False
            existing_signal = _get_path_value(current, SELL_MACD_SIGNAL_TARGET_PATH)
            if existing_signal is not _MISSING:
                if existing_signal == signal:
                    skipped_paths.append(_patch_skipped(path, "sell signal is unchanged"))
                else:
                    skipped_paths.append(_patch_skipped(path, f"target path already exists: {SELL_MACD_SIGNAL_TARGET_PATH}"))
                continue

            patches.append({
                "source_path": SELL_MACD_SIGNAL_PREVIEW_PATH,
                "target_path": SELL_MACD_SIGNAL_TARGET_PATH,
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
            if target_path not in {BUY_EXECUTION_BASE_PATH, BUY_EXECUTION_REPEAT_PATH}:
                skipped_patches.append(_apply_skipped(patch, "unsupported execution policy target path"))
                warnings.append(f"unsupported execution policy target path: {target_path}")
                continue

            value = patch.get("value")
            if not isinstance(value, dict):
                skipped_patches.append(_apply_skipped(patch, "execution policy value is not a dict"))
                continue
            if not _set_path_value(applied_rules_preview, target_path, value):
                skipped_patches.append(_apply_skipped(patch, "target execution policy path is not writable"))
                continue

            applied_patches.append({
                "source_path": patch.get("source_path"),
                "target_path": target_path,
                "operation": operation,
            })
            continue

        if operation == "add_signal":
            if target_path != SELL_MACD_SIGNAL_TARGET_PATH:
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
            signal["enabled"] = False
            signals[APPROVED_SELL_MACD_SIGNAL_KEY] = signal
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

    if operation == "add_signal" and target_path == SELL_MACD_SIGNAL_TARGET_PATH:
        signal = _as_dict(patch.get("signal"))
        diffs.append({
            "path": SELL_MACD_SIGNAL_TARGET_PATH,
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
        RSI_INDICATOR_PATH,
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
        if not candidate_value:
            skipped_paths.append(BUY_EXECUTION_BASE_PATH)
            warnings.append("BUY execution base approval skipped: value is not available")
        elif _set_path_value(approved_rules, BUY_EXECUTION_BASE_PATH, candidate_value):
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
        elif _set_path_value(approved_rules, BUY_EXECUTION_REPEAT_PATH, candidate_value):
            applied_paths.append(BUY_EXECUTION_REPEAT_PATH)
        else:
            skipped_paths.append(BUY_EXECUTION_REPEAT_PATH)
            warnings.append("BUY execution repeat approval skipped: target path is not writable")

    if SELL_MACD_SIGNAL_PREVIEW_PATH in approved_paths:
        sell_candidate = _as_dict(_as_dict(preview_candidates.get("sell")).get("add_signal_candidate"))
        sell_section = approved_rules.setdefault("sell", {})
        if not isinstance(sell_section, dict):
            skipped_paths.append(SELL_MACD_SIGNAL_PREVIEW_PATH)
            warnings.append("sell approval skipped: sell section is not a dict")
        else:
            signals = sell_section.setdefault("signals", {})
            if not isinstance(signals, dict):
                skipped_paths.append(SELL_MACD_SIGNAL_PREVIEW_PATH)
                warnings.append("sell approval skipped: sell.signals is not a dict")
            elif not sell_candidate:
                skipped_paths.append(SELL_MACD_SIGNAL_PREVIEW_PATH)
                warnings.append("sell approval skipped: add_signal_candidate is not available")
            else:
                approved_signal = deepcopy(sell_candidate)
                approved_signal.pop("path", None)
                approved_signal.pop("preview_candidate", None)
                approved_signal["enabled"] = False
                signals[APPROVED_SELL_MACD_SIGNAL_KEY] = approved_signal
                applied_paths.append(SELL_MACD_SIGNAL_PREVIEW_PATH)

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
    fallback_warnings = preview.get("warnings")
    validation_warning_list = validation_warnings if isinstance(validation_warnings, list) else []
    postponed_list = postponed if isinstance(postponed, list) else []
    legacy_notice_list = legacy_notices if isinstance(legacy_notices, list) else []
    if not validation_warning_list and not postponed_list and isinstance(fallback_warnings, list):
        validation_warning_list = fallback_warnings
    warning_list = list(validation_warning_list) + list(postponed_list)
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
        elif path == RSI_INDICATOR_PATH:
            preview_value = _as_dict(_as_dict(preview_candidates.get("indicators")).get("rsi")).get("value", _MISSING)
        elif path == SELL_MACD_SIGNAL_PREVIEW_PATH:
            preview_value = _as_dict(_as_dict(preview_candidates.get("sell")).get("add_signal_candidate"))
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
        elif path in {BUY_EXECUTION_BASE_PATH, BUY_EXECUTION_REPEAT_PATH} and preview_exists:
            status = "changed" if current_exists else "added"
        elif path == SELL_MACD_SIGNAL_PREVIEW_PATH and preview_exists:
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
        "warnings": list(warning_list),
    }
