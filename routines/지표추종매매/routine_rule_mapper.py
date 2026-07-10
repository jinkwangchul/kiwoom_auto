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


def _truthy_ui(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on", "checked", "enabled", "사용", "활성"}


def _optional_filter_enabled(values: dict[str, Any], enabled_key: str, value_key: str) -> bool:
    if enabled_key in values:
        return _truthy_ui(values.get(enabled_key))
    return values.get(value_key) not in (None, "")


def _build_buy_ma_conditions(signal_filter: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]]:
    if not _optional_filter_enabled(signal_filter, "buy_ma_enabled", "buy_ma_value_line"):
        return []

    period = _safe_int(signal_filter.get("buy_ma_value_line"))
    if period is None or period <= 0:
        warnings.append("buy MA period is not numeric")
        return []

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
        return []

    return [{
        "enabled": True,
        "not": False,
        "target": "CLOSE",
        "operator": operator,
        "compare_target": "MA",
        "period": period,
        "description": "UI preview: buy price/MA condition",
    }]


def _build_buy_bollinger_conditions(signal_filter: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]]:
    if not _optional_filter_enabled(signal_filter, "buy_bollinger_enabled", "buy_bollinger_value_line"):
        return []

    threshold = _safe_float(signal_filter.get("buy_bollinger_value_line"))
    operator = _compare_operator(signal_filter.get("buy_bollinger_compare_combo"))
    if threshold is None:
        warnings.append("buy Bollinger threshold is not numeric")
        return []
    if operator is None:
        warnings.append(f"buy Bollinger compare is not mapped: {signal_filter.get('buy_bollinger_compare_combo')!r}")
        return []

    direction = str(signal_filter.get("buy_bollinger_direction_combo") or "").strip()
    signed_threshold = -abs(threshold) if direction == "\ud558\ud5a5" else abs(threshold)
    return [{
        "enabled": True,
        "not": False,
        "target": "CLOSE",
        "operator": operator,
        "value": signed_threshold,
        "description": "UI preview: buy Bollinger threshold condition",
    }]


def _build_buy_price_compare_conditions(price_compare: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]]:
    if not _optional_filter_enabled(price_compare, "enabled", "ratio_line"):
        return []

    type_text = str(price_compare.get("type_combo") or "").strip()
    if type_text and type_text != "\uac00\uaca9\ube44\uad50":
        return []

    target = _series_target(price_compare.get("left_combo"))
    compare_target = _series_target(price_compare.get("right_combo"))
    threshold = _safe_float(price_compare.get("ratio_line"))
    operator = _compare_operator(price_compare.get("compare_combo"))
    if target is None:
        warnings.append(f"buy price compare left target is not mapped: {price_compare.get('left_combo')!r}")
        return []
    if compare_target is None:
        warnings.append(f"buy price compare right target is not mapped: {price_compare.get('right_combo')!r}")
        return []
    if threshold is None:
        warnings.append("buy price compare ratio is not numeric")
        return []
    if operator is None:
        warnings.append(f"buy price compare operator is not mapped: {price_compare.get('compare_combo')!r}")
        return []

    return [{
        "enabled": True,
        "not": False,
        "target": target,
        "operator": operator,
        "compare_target": compare_target,
        "value": threshold,
        "description": "UI preview: buy price compare condition",
    }]


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
    warnings: list[str] = []
    preview_rules = deepcopy(current_rules) if isinstance(current_rules, dict) else {}
    source_rules = current_rules if isinstance(current_rules, dict) else {}
    preview_rules["bar"] = {}
    preview_candidates: dict[str, Any] = {}
    state = _as_dict(ui_state)

    basic = _as_dict(state.get("basic"))
    bar_minutes = _safe_int(basic.get("basic_signal_interval_combo"))
    if bar_minutes is None:
        warnings.append("basic signal interval is not numeric; bar.bar_minutes not mapped")
    else:
        preview_rules["bar"]["bar_minutes"] = bar_minutes
        preview_candidates["bar"] = {
            "path": BAR_MINUTES_PATH,
            "value": bar_minutes,
        }

    buy_ui = _as_dict(state.get("buy_ui"))
    signal_filter = _as_dict(buy_ui.get("signal_filter"))
    price_compare = _as_dict(buy_ui.get("price_compare"))
    buy_conditions = _build_buy_osc_conditions(signal_filter, warnings)
    buy_conditions.extend(_build_buy_ma_conditions(signal_filter, warnings))
    buy_conditions.extend(_build_buy_bollinger_conditions(signal_filter, warnings))
    buy_conditions.extend(_build_buy_price_compare_conditions(price_compare, warnings))
    if buy_conditions:
        buy_candidate = _build_buy_merge_candidate(source_rules, buy_conditions, warnings)
        if buy_candidate:
            preview_candidates["buy"] = buy_candidate
    else:
        warnings.append("buy OCR/OSC candidate group was not generated")

    rsi_candidate = _build_rsi_indicator_candidate(source_rules, signal_filter, warnings)
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
    sell_indicator_condition = _build_sell_condition_c_indicator_condition(condition_c, warnings)
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
        warnings.append("sell condition C MACD is an add_signal_candidate and does not replace existing macd_sell")
    else:
        warnings.append("sell condition C MACD candidate group was not generated")

    preview_rules["indicator_follow_rule_preview"] = {
        "mode": "merge_add_candidate",
        "candidates": preview_candidates,
    }

    warnings.extend([
        "buy method mapping is postponed",
        "repeat buy mapping is postponed",
        "price compare buy mapping is postponed",
        "situation response mapping is postponed",
        "additional feature mapping is postponed",
        "cycle setting mapping is postponed",
        "exit condition mapping is postponed",
        "sell method A/B/C mapping is postponed",
        "pending order policy mapping is postponed",
        "completion policy mapping is postponed",
    ])

    return {
        "preview_rules": preview_rules,
        "mapped_paths": [
            BAR_MINUTES_PATH,
            BUY_CONDITIONS_PATH,
            RSI_INDICATOR_PATH,
            SELL_MACD_SIGNAL_PREVIEW_PATH,
        ],
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
    known_paths = {BAR_MINUTES_PATH, BUY_CONDITIONS_PATH, RSI_INDICATOR_PATH, SELL_MACD_SIGNAL_PREVIEW_PATH}
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
    warnings = preview.get("warnings")
    paths = mapped_paths if isinstance(mapped_paths, list) else []
    warning_list = warnings if isinstance(warnings, list) else []
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
        "postponed": len(warning_list),
    }
    changes: list[dict[str, Any]] = []

    for path in paths:
        if not isinstance(path, str):
            continue

        current_value = _get_path_value(current, path)
        if path == BUY_CONDITIONS_PATH:
            preview_value = _as_dict(preview_candidates.get("buy"))
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
        "warnings": list(warning_list),
    }
