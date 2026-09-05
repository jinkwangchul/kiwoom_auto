"""Routine-owned committed-rules validation for indicator-follow rules."""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any

def _path_exists(data: dict[str, Any], path: str) -> bool:
    current: Any = data
    for part in path.split("."):
        if "[" in part and part.endswith("]"):
            name, index_text = part[:-1].split("[", 1)
            if not isinstance(current, dict) or name not in current:
                return False
            current = current[name]
            try:
                index = int(index_text)
            except ValueError:
                return False
            if not isinstance(current, list) or index < 0 or index >= len(current):
                return False
            current = current[index]
        else:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
    return True


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if "[" in part and part.endswith("]"):
            name, index_text = part[:-1].split("[", 1)
            current = current[name][int(index_text)]
        else:
            current = current[part]
    return current


def _condition_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("target") == right.get("target")
        and left.get("operator") == right.get("operator")
        and left.get("compare_target") == right.get("compare_target")
        and left.get("period") == right.get("period")
        and left.get("value") == right.get("value")
    )


def _condition_key(condition: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        condition.get("target"),
        condition.get("operator"),
        condition.get("compare_target"),
        condition.get("period"),
        condition.get("value"),
    )


def _without_key(value: Any, key: str) -> Any:
    copied = deepcopy(value)
    if isinstance(copied, dict):
        copied.pop(key, None)
    return copied


def _remove_one_matching_condition(conditions: list[Any], condition: dict[str, Any]) -> tuple[list[Any], bool]:
    remaining = []
    removed = False
    for existing in conditions:
        if not removed and isinstance(existing, dict) and _condition_matches(existing, condition):
            removed = True
            continue
        remaining.append(existing)
    return remaining, removed


def _diff_paths(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "<root>"]
    if isinstance(left, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right), key=str):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                paths.append(child_path)
            else:
                paths.extend(_diff_paths(left[key], right[key], child_path))
        return paths
    if isinstance(left, list):
        paths = []
        if len(left) != len(right):
            paths.append(path or "<root>")
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            paths.extend(_diff_paths(left_item, right_item, f"{path}[{index}]"))
        return paths
    return [] if left == right else [path or "<root>"]


def validate_committed_rules(
    pre_rules: dict[str, Any],
    post_rules: dict[str, Any],
    final_diff: list[Any],
    safety_checks: dict[str, Any],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    unexpected_changes: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    def add_unexpected(path: str, reason: str) -> None:
        unexpected_changes.append({"path": path, "reason": reason})

    add_check("json_root_dict", isinstance(post_rules, dict))
    add_check("buy_conditions_exists", _path_exists(post_rules, "buy.groups[0].conditions"))
    timeout_path = "buy.execution.base.unfilled_timeout_policy"
    if _path_exists(post_rules, timeout_path):
        policy = _get_path(post_rules, timeout_path)
        valid = isinstance(policy, dict) and policy.get("enabled") is False
        if isinstance(policy, dict) and policy.get("enabled") is True:
            value = policy.get("configured_value")
            valid = (policy.get("policy") == "CANCEL_PENDING_ORDER" and policy.get("action") == "CANCEL"
                     and policy.get("scope") in {"EACH", "BATCH"}
                     and policy.get("configured_unit") in {"SECOND", "MINUTE", "BAR"}
                     and isinstance(value, (int, float)) and not isinstance(value, bool)
                     and isfinite(value) and value >= 0)
        add_check("buy_unfilled_timeout_policy_valid", valid)
        if not valid:
            add_unexpected(timeout_path, "invalid BUY timeout/cancel policy")
    reset_path = "buy.execution.base.buy_price_reset_policy"
    if _path_exists(post_rules, reset_path):
        policy = _get_path(post_rules, reset_path)
        valid = isinstance(policy, dict) and policy.get("enabled") is False
        if isinstance(policy, dict) and policy.get("enabled") is True:
            threshold = policy.get("threshold_percent")
            valid = (
                policy.get("policy") == "BUY_PRICE_CHANGE_RESET"
                and policy.get("action") == "RESET"
                and policy.get("left_source") in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
                and policy.get("right_source") in {"ORDER_PRICE", "CURRENT_PRICE", "AVG_PRICE"}
                and policy.get("direction") in {"UP", "DOWN", "BOTH"}
                and policy.get("compare") in {">=", "<=", "WITHIN", "OUTSIDE"}
                and isinstance(threshold, (int, float))
                and not isinstance(threshold, bool)
                and isfinite(threshold)
                and threshold > 0
            )
        add_check("buy_price_reset_policy_valid", valid)
        if not valid:
            add_unexpected(reset_path, "invalid BUY price-reset policy")
    exit_path = "buy.execution.base.buy_exit_policy"
    if _path_exists(post_rules, exit_path):
        policy = _get_path(post_rules, exit_path)
        valid = isinstance(policy, dict) and policy.get("enabled") is False
        if isinstance(policy, dict) and policy.get("enabled") is True:
            conditions = policy.get("conditions")
            valid = (
                policy.get("policy") == "BUY_REPEAT_EXIT"
                and policy.get("logic") == "OR"
                and policy.get("completion_behavior") == "BLOCK_FUTURE_BUY_ROUNDS"
                and isinstance(conditions, list) and bool(conditions)
                and all(isinstance(item, dict) and item.get("condition_type") in {"COUNT", "TIME", "PRICE"} for item in conditions)
            )
        add_check("buy_exit_policy_valid", valid)
        if not valid:
            add_unexpected(exit_path, "invalid BUY repeat-exit policy")
    last_round_active_path = "buy.execution.base.last_round_active_buy"
    if _path_exists(post_rules, last_round_active_path):
        policy = _get_path(post_rules, last_round_active_path)
        enabled = policy.get("enabled") if isinstance(policy, dict) else None
        ratio = policy.get("ratio_percent") if isinstance(policy, dict) else None
        valid = (
            isinstance(policy, dict)
            and isinstance(enabled, bool)
            and policy.get("applies_to") == "LAST_MULTI_POINT_CHILD"
            and policy.get("budget_policy_override") == "NONE"
            and policy.get("purpose") == "BUY_METHOD_SPECIAL_ACTION"
            and policy.get("subject") == "AVERAGE_PRICE"
            and policy.get("reference") == "MULTI_POINT_SET_PRICE"
            and policy.get("direction") in {"UP", "DOWN", "BOTH"}
            and isinstance(ratio, (int, float)) and not isinstance(ratio, bool)
            and isfinite(ratio) and ratio >= 0
            and policy.get("comparator") in {">=", "<=", "WITHIN", "OUTSIDE"}
        )
        add_check("buy_last_round_active_policy_valid", valid)
        add_check("buy_last_round_active_execution_connected", valid)
        if not valid:
            add_unexpected(last_round_active_path, "invalid BUY last-round active policy")

    additional_path = "buy.execution.additional"
    if _path_exists(post_rules, additional_path):
        policy = _get_path(post_rules, additional_path)
        price = policy.get("previous_round_price_skip") if isinstance(policy, dict) else None
        last = policy.get("last_plus_one") if isinstance(policy, dict) else None
        price_enabled = price.get("enabled") if isinstance(price, dict) else None
        last_enabled = last.get("enabled") if isinstance(last, dict) else None
        price_ratio = price.get("ratio_percent") if isinstance(price, dict) else None
        active = last.get("active_condition") if isinstance(last, dict) else None
        active_ratio = active.get("ratio_percent") if isinstance(active, dict) else None
        valid = (
            isinstance(policy, dict)
            and isinstance(price, dict)
            and isinstance(last, dict)
            and isinstance(price_enabled, bool)
            and isinstance(last_enabled, bool)
            and price.get("reference_source") == "PREVIOUS_CONFIRMED_BUY_ORDER_PRICE"
            and price.get("current_source") == "ACTIONABLE_ORDER_PRICE"
            and price.get("action") == "SKIP_CURRENT_GENERATION"
            and price.get("skipped_round_increment") is False
            and last.get("generation_kind") == "LAST_PLUS_ONE"
            and last.get("trigger") == "AFTER_NORMAL_MAX_ROUND_COMPLETED"
            and last.get("max_occurrences") == 1
            and last.get("method") in {"MARKET", "CURRENT_PRICE", "ACTIVE"}
            and last.get("budget_basis") == "LAST_NORMAL_ROUND_APPROVED_BUDGET"
            and last.get("terminal_after_completed_fill") is True
            and price.get("direction") in {"UP", "DOWN", "BOTH"}
            and isinstance(price_ratio, (int, float)) and not isinstance(price_ratio, bool)
            and isfinite(price_ratio) and price_ratio >= 0
            and price.get("comparator") in {">=", "<=", "WITHIN", "OUTSIDE"}
            and isinstance(active, dict)
            and active.get("direction") in {"UP", "DOWN", "BOTH"}
            and isinstance(active_ratio, (int, float)) and not isinstance(active_ratio, bool)
            and isfinite(active_ratio) and active_ratio >= 0
            and active.get("comparator") in {">=", "<=", "WITHIN", "OUTSIDE"}
        )
        execution_connected = policy.get("execution_connected") is True
        add_check("buy_additional_policy_valid", valid)
        add_check("buy_additional_execution_connected", execution_connected)
        if not valid:
            add_unexpected(additional_path, "invalid BUY additional policy")
        if not execution_connected:
            add_unexpected(additional_path, "BUY additional execution consumer is not connected")

    cycle_path = "buy.execution.cycle"
    if _path_exists(post_rules, cycle_path):
        policy = _get_path(post_rules, cycle_path)
        situation = policy.get("situation_response") if isinstance(policy, dict) else None
        unsupported_cancel_batch = (
            isinstance(situation, dict)
            and situation.get("mode") == "PRICE_COMPARE"
            and situation.get("action") == "CANCEL_BATCH"
        )
        connected = policy.get("execution_connected") is True if isinstance(policy, dict) else False
        valid = (
            isinstance(policy, dict)
            and policy.get("scope") == "SIGNAL_SCOPED_BUY_CYCLE"
            and policy.get("requires_source_signal") is True
            and policy.get("autonomous_scheduler") is False
            and policy.get("after_cycle_completion") == "REQUIRE_NEW_BUY_SIGNAL"
            and isinstance(policy.get("order_policy"), dict)
            and isinstance(policy.get("point_policy"), dict)
            and isinstance(situation, dict)
            and connected
            and not unsupported_cancel_batch
            and policy.get("execution_lock_reason") in {"", None}
        )
        add_check("buy_cycle_policy_valid", valid)
        add_check("buy_cycle_execution_connected", connected and not unsupported_cancel_batch)
        if not valid:
            add_unexpected(cycle_path, "invalid BUY cycle policy")
        if unsupported_cancel_batch or not connected:
            add_unexpected(cycle_path, "CYCLE_OPTION_EXECUTION_NOT_CONNECTED")
    if _path_exists(post_rules, "sell.method.selected_sets"):
        selected_sets = _get_path(post_rules, "sell.method.selected_sets")
        selected_sets_valid = (
            isinstance(selected_sets, list)
            and len(selected_sets) == 1
            and selected_sets[0] in {"setting_a", "setting_b", "setting_c"}
        )
        add_check(
            "sell_method_exactly_one_selected_set",
            selected_sets_valid,
            str(selected_sets),
        )
        selected_setting_valid = (
            selected_sets_valid
            and _path_exists(post_rules, f"sell.method.{selected_sets[0]}")
            and isinstance(_get_path(post_rules, f"sell.method.{selected_sets[0]}"), dict)
        )
        add_check(
            "sell_method_selected_setting_exists",
            selected_setting_valid,
            str(selected_sets),
        )
        if not selected_sets_valid:
            add_unexpected(
                "sell.method.selected_sets",
                "exactly one supported SELL method set is required",
            )
        elif not selected_setting_valid:
            add_unexpected(
                f"sell.method.{selected_sets[0]}",
                "selected SELL method setting is required",
            )

    pre_buy_groups = deepcopy(pre_rules.get("buy", {}).get("groups"))
    post_buy_groups = deepcopy(post_rules.get("buy", {}).get("groups"))
    if isinstance(pre_buy_groups, list) and isinstance(post_buy_groups, list):
        add_check("buy_groups_not_replaced", len(pre_buy_groups) == len(post_buy_groups))
    else:
        add_check("buy_groups_not_replaced", False, "buy.groups missing or not a list")

    pre_conditions = []
    post_conditions = []
    if _path_exists(pre_rules, "buy.groups[0].conditions"):
        pre_conditions = _get_path(pre_rules, "buy.groups[0].conditions")
    if _path_exists(post_rules, "buy.groups[0].conditions"):
        post_conditions = _get_path(post_rules, "buy.groups[0].conditions")
    pre_conditions = pre_conditions if isinstance(pre_conditions, list) else []
    post_conditions = post_conditions if isinstance(post_conditions, list) else []

    allowed_buy_conditions = [
        deepcopy(diff.get("condition"))
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "merge_conditions"
        and diff.get("path") == "buy.groups[0].conditions"
        and isinstance(diff.get("condition"), dict)
    ]
    allowed_sell_signal_paths = {
        "sell.signals.ui_condition_a": "ui_condition_a",
        "sell.signals.ui_condition_b": "ui_condition_b",
        "sell.signals.ui_condition_c": "ui_condition_c",
        "sell.signals.ui_condition_c_macd_sell": "ui_condition_c_macd_sell",
    }
    allowed_sell_signal_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "add_signal"
        and diff.get("path") in allowed_sell_signal_paths
    ]
    allowed_profit_rate_signal_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_signal"
        and diff.get("path") == "sell.signals.profit_rate_sell"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_bar_minutes_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_value"
        and diff.get("path") == "bar.bar_minutes"
    ]
    allowed_signal_runtime_policy_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_signal_runtime_policy"
        and diff.get("path") == "signal_runtime_policy"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_rsi_indicator_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_indicator"
        and diff.get("path") == "indicators.rsi"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_buy_ma_filter_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_filter"
        and diff.get("path") == "buy.filters.moving_average"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_buy_price_compare_filter_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_filter"
        and diff.get("path") == "buy.filters.price_compare"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_buy_bollinger_filter_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_filter"
        and diff.get("path") == "buy.filters.bollinger"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_buy_ocr_filter_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_filter"
        and diff.get("path") == "buy.filters.ocr"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_buy_rsi_filter_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_filter"
        and diff.get("path") == "buy.filters.rsi"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_buy_composite_filter_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_filter"
        and diff.get("path") == "buy.filters.composite"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_buy_execution_base_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_execution_policy"
        and diff.get("path") == "buy.execution.base"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_buy_execution_repeat_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_execution_policy"
        and diff.get("path") == "buy.execution.repeat"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_buy_execution_additional_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_execution_policy"
        and diff.get("path") == "buy.execution.additional"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_buy_execution_cycle_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_execution_policy"
        and diff.get("path") == "buy.execution.cycle"
        and isinstance(diff.get("value"), dict)
    ]
    allowed_sell_method_paths = {
        "sell.method.selected_sets",
        "sell.method.setting_a",
        "sell.method.setting_b",
        "sell.method.setting_c",
    }
    allowed_sell_method_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_method_policy"
        and diff.get("path") in allowed_sell_method_paths
        and "value" in diff
    ]

    if any(isinstance(condition, dict) and condition.get("target") == "OSC" and condition.get("operator") == "TURN_UP" for condition in pre_conditions):
        add_check(
            "existing_osc_turn_up_preserved",
            any(
                isinstance(condition, dict)
                and condition.get("target") == "OSC"
                and condition.get("operator") == "TURN_UP"
                for condition in post_conditions
            ),
        )

    if _path_exists(pre_rules, "sell.signals.macd_sell"):
        add_check(
            "macd_sell_unchanged",
            _path_exists(post_rules, "sell.signals.macd_sell")
            and _get_path(pre_rules, "sell.signals.macd_sell") == _get_path(post_rules, "sell.signals.macd_sell"),
        )

    for key in ("rules_json_write", "engine_connected", "buy_groups_replace", "macd_sell_replace"):
        add_check(f"safety_{key}", safety_checks.get(key) is False)

    for diff in final_diff:
        if not isinstance(diff, dict):
            continue
        operation = diff.get("operation")
        if operation == "merge_conditions":
            condition = diff.get("condition")
            add_check(
                "final_diff_buy_condition_exists",
                isinstance(condition, dict)
                and any(isinstance(existing, dict) and _condition_matches(existing, condition) for existing in post_conditions),
            )
        if operation == "set_value":
            path = str(diff.get("path") or "")
            if path == "bar.bar_minutes":
                add_check(
                    "final_diff_bar_minutes_matches",
                    _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value"),
                )
        if operation == "set_indicator":
            path = str(diff.get("path") or "")
            if path == "indicators.rsi":
                add_check(
                    "final_diff_rsi_indicator_matches",
                    _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value"),
                )
        if operation == "set_signal_runtime_policy":
            path = str(diff.get("path") or "")
            add_check(
                "final_diff_signal_runtime_policy_matches",
                path == "signal_runtime_policy"
                and _path_exists(post_rules, path)
                and _get_path(post_rules, path) == diff.get("value"),
            )
        if operation == "set_filter":
            path = str(diff.get("path") or "")
            if path == "buy.filters.moving_average":
                add_check(
                    "final_diff_buy_ma_filter_matches",
                    _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value"),
                )
            if path == "buy.filters.price_compare":
                add_check(
                    "final_diff_buy_price_compare_filter_matches",
                    _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value"),
                )
            if path == "buy.filters.bollinger":
                add_check(
                    "final_diff_buy_bollinger_filter_matches",
                    _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value"),
                )
            if path == "buy.filters.ocr":
                matches = _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value")
                add_check(
                    "final_diff_buy_ocr_filter_matches",
                    matches,
                )
                if not matches:
                    add_unexpected(path, "final_diff buy OCR filter missing or changed in post rules")
            if path == "buy.filters.rsi":
                matches = _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value")
                add_check(
                    "final_diff_buy_rsi_filter_matches",
                    matches,
                )
                if not matches:
                    add_unexpected(path, "final_diff buy RSI filter missing or changed in post rules")
            if path == "buy.filters.composite":
                matches = _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value")
                add_check(
                    "final_diff_buy_composite_filter_matches",
                    matches,
                )
                if not matches:
                    add_unexpected(path, "final_diff buy composite filter missing or changed in post rules")
        if operation == "set_execution_policy":
            path = str(diff.get("path") or "")
            if path == "buy.execution.base":
                add_check(
                    "final_diff_buy_execution_base_matches",
                    _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value"),
                )
            elif path == "buy.execution.repeat":
                add_check(
                    "final_diff_buy_execution_repeat_matches",
                    _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value"),
                )
            elif path == "buy.execution.additional":
                add_check(
                    "final_diff_buy_execution_additional_matches",
                    _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value"),
                )
            elif path == "buy.execution.cycle":
                add_check(
                    "final_diff_buy_execution_cycle_matches",
                    _path_exists(post_rules, path) and _get_path(post_rules, path) == diff.get("value"),
                )
            else:
                add_check("final_diff_buy_execution_policy_path_allowed", False, path)
                add_unexpected(path or "<missing>", "unsupported buy.execution policy path")
        if operation == "add_signal":
            path = str(diff.get("path") or "")
            signal_exists = _path_exists(post_rules, path)
            signal = _get_path(post_rules, path) if signal_exists else None
            add_check("final_diff_sell_signal_exists", signal_exists)
            add_check(
                "final_diff_sell_signal_executable",
                isinstance(signal, dict) and signal.get("enabled") is True,
            )
            add_check("final_diff_sell_macd_preserved", _path_exists(post_rules, "sell.signals.macd_sell"))
        if operation == "set_signal":
            path = str(diff.get("path") or "")
            value = diff.get("value")
            if path == "sell.signals.profit_rate_sell" and isinstance(value, dict):
                signal_exists = _path_exists(post_rules, path)
                signal = _get_path(post_rules, path) if signal_exists else None
                allowed_fields = {"enabled", "profit_rate_percent", "basis"}
                unsupported_fields = set(value) - allowed_fields
                add_check("final_diff_profit_rate_signal_exists", signal_exists)
                add_check("final_diff_profit_rate_signal_fields_allowed", not unsupported_fields)
                if unsupported_fields:
                    add_unexpected(path, f"unsupported profit_rate_sell fields: {sorted(unsupported_fields)}")
                matches = (
                    isinstance(signal, dict)
                    and all(signal.get(key) == value.get(key) for key in value)
                )
                add_check("final_diff_profit_rate_signal_matches", matches)
                if not matches:
                    add_unexpected(path, "profit_rate_sell allowed fields missing or changed in post rules")
            else:
                add_check("final_diff_sell_set_signal_path_allowed", False, path)
                add_unexpected(path or "<missing>", "unsupported sell set_signal path")
        if operation == "set_method_policy":
            path = str(diff.get("path") or "")
            value = diff.get("value")
            if path in allowed_sell_method_paths:
                matches = _path_exists(post_rules, path) and _get_path(post_rules, path) == value
                add_check("final_diff_sell_method_policy_matches", matches)
                if not matches:
                    add_unexpected(path, "sell method policy missing or changed in post rules")
            else:
                add_check("final_diff_sell_method_policy_path_allowed", False, path)
                add_unexpected(path or "<missing>", "unsupported sell method policy path")

    if isinstance(pre_buy_groups, list) and isinstance(post_buy_groups, list) and pre_buy_groups and post_buy_groups:
        add_check("buy_non_target_groups_unchanged", pre_buy_groups[1:] == post_buy_groups[1:])
        if pre_buy_groups[1:] != post_buy_groups[1:]:
            add_unexpected("buy.groups[1:]", "non-target buy groups changed")

        pre_group0_metadata = _without_key(pre_buy_groups[0], "conditions")
        post_group0_metadata = _without_key(post_buy_groups[0], "conditions")
        add_check("buy_group0_metadata_unchanged", pre_group0_metadata == post_group0_metadata)
        if pre_group0_metadata != post_group0_metadata:
            add_unexpected("buy.groups[0]", "buy group metadata changed outside conditions")

        normalized_post_conditions = deepcopy(post_conditions)
        for allowed_condition in allowed_buy_conditions:
            normalized_post_conditions, removed = _remove_one_matching_condition(
                normalized_post_conditions,
                allowed_condition,
            )
            add_check("allowed_buy_condition_added", removed, str(allowed_condition))
            if not removed:
                add_unexpected("buy.groups[0].conditions", "final_diff buy condition missing from post rules")

        add_check("existing_buy_conditions_unchanged", normalized_post_conditions == pre_conditions)
        if normalized_post_conditions != pre_conditions:
            add_unexpected("buy.groups[0].conditions", "existing buy conditions changed or unapproved condition added")

        condition_keys = [
            _condition_key(condition)
            for condition in post_conditions
            if isinstance(condition, dict)
        ]
        duplicate_free = len(condition_keys) == len(set(condition_keys))
        add_check("buy_conditions_no_duplicate_target_operator_value", duplicate_free)
        if not duplicate_free:
            add_unexpected("buy.groups[0].conditions", "duplicate buy condition target/operator/value")

    pre_signals = deepcopy(pre_rules.get("sell", {}).get("signals"))
    post_signals = deepcopy(post_rules.get("sell", {}).get("signals"))
    if isinstance(pre_signals, dict) and isinstance(post_signals, dict):
        for key, pre_signal in pre_signals.items():
            if key not in post_signals:
                add_check(f"existing_sell_signal_present:{key}", False)
                add_unexpected(f"sell.signals.{key}", "existing sell signal deleted")
            else:
                if key == "profit_rate_sell" and allowed_profit_rate_signal_diffs:
                    expected_signal = deepcopy(pre_signal) if isinstance(pre_signal, dict) else pre_signal
                    if isinstance(expected_signal, dict):
                        for diff in allowed_profit_rate_signal_diffs:
                            for field, value in diff.get("value", {}).items():
                                if field in {"enabled", "profit_rate_percent", "basis"}:
                                    expected_signal[field] = value
                    unchanged = post_signals.get(key) == expected_signal
                else:
                    unchanged = post_signals.get(key) == pre_signal
                add_check(f"existing_sell_signal_unchanged:{key}", unchanged)
                if not unchanged:
                    add_unexpected(f"sell.signals.{key}", "existing sell signal changed")

        extra_signal_keys = set(post_signals) - set(pre_signals)
        allowed_extra_keys = {
            allowed_sell_signal_paths[str(diff.get("path"))]
            for diff in allowed_sell_signal_diffs
            if str(diff.get("path")) in allowed_sell_signal_paths
        }
        disallowed_extra_keys = extra_signal_keys - allowed_extra_keys
        add_check("sell_extra_signals_only_allowed_candidate", not disallowed_extra_keys)
        for key in sorted(disallowed_extra_keys):
            add_unexpected(f"sell.signals.{key}", "unapproved new sell signal added")

        for diff in allowed_sell_signal_diffs:
            allowed_sell_signal_path = str(diff.get("path"))
            allowed_sell_signal_key = allowed_sell_signal_paths.get(allowed_sell_signal_path, "")
            signal = post_signals.get(allowed_sell_signal_key)
            add_check(f"allowed_sell_signal_exists:{allowed_sell_signal_key}", isinstance(signal, dict))
            if not isinstance(signal, dict):
                add_unexpected(allowed_sell_signal_path, "final_diff sell signal missing from post rules")
            else:
                enabled_true = signal.get("enabled") is True
                no_preview_candidate = "preview_candidate" not in signal
                add_check(f"allowed_sell_signal_executable:{allowed_sell_signal_key}", enabled_true)
                add_check(f"allowed_sell_signal_not_preview_candidate:{allowed_sell_signal_key}", no_preview_candidate)
                if not enabled_true:
                    add_unexpected(allowed_sell_signal_path, "allowed sell signal is not executable")
                if not no_preview_candidate:
                    add_unexpected(allowed_sell_signal_path, "allowed sell signal contains preview_candidate")
    else:
        add_check("sell_signals_dict", False, "sell.signals missing or not a dict")
        add_unexpected("sell.signals", "sell signals structure changed")

    pre_normalized = deepcopy(pre_rules)
    post_normalized = deepcopy(post_rules)
    if allowed_bar_minutes_diffs and _path_exists(pre_normalized, "bar.bar_minutes") and _path_exists(post_normalized, "bar.bar_minutes"):
        _get_path(post_normalized, "bar")["bar_minutes"] = deepcopy(_get_path(pre_normalized, "bar.bar_minutes"))
    if allowed_signal_runtime_policy_diffs and _path_exists(post_normalized, "signal_runtime_policy"):
        if _path_exists(pre_normalized, "signal_runtime_policy"):
            post_normalized["signal_runtime_policy"] = deepcopy(pre_normalized["signal_runtime_policy"])
        else:
            post_normalized.pop("signal_runtime_policy", None)
    if allowed_rsi_indicator_diffs and _path_exists(pre_normalized, "indicators.rsi") and _path_exists(post_normalized, "indicators.rsi"):
        _get_path(post_normalized, "indicators")["rsi"] = deepcopy(_get_path(pre_normalized, "indicators.rsi"))
    if allowed_buy_ma_filter_diffs and _path_exists(post_normalized, "buy.filters.moving_average"):
        if _path_exists(pre_normalized, "buy.filters.moving_average"):
            _get_path(post_normalized, "buy.filters")["moving_average"] = deepcopy(
                _get_path(pre_normalized, "buy.filters.moving_average")
            )
        elif _path_exists(post_normalized, "buy.filters"):
            _get_path(post_normalized, "buy.filters").pop("moving_average", None)
            if _get_path(post_normalized, "buy.filters") == {} and not _path_exists(pre_normalized, "buy.filters"):
                _get_path(post_normalized, "buy").pop("filters", None)
    if allowed_buy_price_compare_filter_diffs and _path_exists(post_normalized, "buy.filters.price_compare"):
        if _path_exists(pre_normalized, "buy.filters.price_compare"):
            _get_path(post_normalized, "buy.filters")["price_compare"] = deepcopy(
                _get_path(pre_normalized, "buy.filters.price_compare")
            )
        elif _path_exists(post_normalized, "buy.filters"):
            _get_path(post_normalized, "buy.filters").pop("price_compare", None)
            if _get_path(post_normalized, "buy.filters") == {} and not _path_exists(pre_normalized, "buy.filters"):
                _get_path(post_normalized, "buy").pop("filters", None)
    if allowed_buy_bollinger_filter_diffs and _path_exists(post_normalized, "buy.filters.bollinger"):
        if _path_exists(pre_normalized, "buy.filters.bollinger"):
            _get_path(post_normalized, "buy.filters")["bollinger"] = deepcopy(
                _get_path(pre_normalized, "buy.filters.bollinger")
            )
        elif _path_exists(post_normalized, "buy.filters"):
            _get_path(post_normalized, "buy.filters").pop("bollinger", None)
            if _get_path(post_normalized, "buy.filters") == {} and not _path_exists(pre_normalized, "buy.filters"):
                _get_path(post_normalized, "buy").pop("filters", None)
    if allowed_buy_ocr_filter_diffs and _path_exists(post_normalized, "buy.filters.ocr"):
        if _path_exists(pre_normalized, "buy.filters.ocr"):
            _get_path(post_normalized, "buy.filters")["ocr"] = deepcopy(
                _get_path(pre_normalized, "buy.filters.ocr")
            )
        elif _path_exists(post_normalized, "buy.filters"):
            _get_path(post_normalized, "buy.filters").pop("ocr", None)
            if _get_path(post_normalized, "buy.filters") == {} and not _path_exists(pre_normalized, "buy.filters"):
                _get_path(post_normalized, "buy").pop("filters", None)
    if allowed_buy_rsi_filter_diffs and _path_exists(post_normalized, "buy.filters.rsi"):
        if _path_exists(pre_normalized, "buy.filters.rsi"):
            _get_path(post_normalized, "buy.filters")["rsi"] = deepcopy(
                _get_path(pre_normalized, "buy.filters.rsi")
            )
        elif _path_exists(post_normalized, "buy.filters"):
            _get_path(post_normalized, "buy.filters").pop("rsi", None)
            if _get_path(post_normalized, "buy.filters") == {} and not _path_exists(pre_normalized, "buy.filters"):
                _get_path(post_normalized, "buy").pop("filters", None)
    if allowed_buy_composite_filter_diffs and _path_exists(post_normalized, "buy.filters.composite"):
        if _path_exists(pre_normalized, "buy.filters.composite"):
            _get_path(post_normalized, "buy.filters")["composite"] = deepcopy(
                _get_path(pre_normalized, "buy.filters.composite")
            )
        elif _path_exists(post_normalized, "buy.filters"):
            _get_path(post_normalized, "buy.filters").pop("composite", None)
            if _get_path(post_normalized, "buy.filters") == {} and not _path_exists(pre_normalized, "buy.filters"):
                _get_path(post_normalized, "buy").pop("filters", None)
    if allowed_buy_execution_base_diffs and _path_exists(post_normalized, "buy.execution.base"):
        if _path_exists(pre_normalized, "buy.execution.base"):
            _get_path(post_normalized, "buy.execution")["base"] = deepcopy(
                _get_path(pre_normalized, "buy.execution.base")
            )
        elif _path_exists(post_normalized, "buy.execution"):
            _get_path(post_normalized, "buy.execution").pop("base", None)
            if _get_path(post_normalized, "buy.execution") == {} and not _path_exists(pre_normalized, "buy.execution"):
                _get_path(post_normalized, "buy").pop("execution", None)
    if allowed_buy_execution_repeat_diffs and _path_exists(post_normalized, "buy.execution.repeat"):
        if _path_exists(pre_normalized, "buy.execution.repeat"):
            _get_path(post_normalized, "buy.execution")["repeat"] = deepcopy(
                _get_path(pre_normalized, "buy.execution.repeat")
            )
        elif _path_exists(post_normalized, "buy.execution"):
            _get_path(post_normalized, "buy.execution").pop("repeat", None)
            if _get_path(post_normalized, "buy.execution") == {} and not _path_exists(pre_normalized, "buy.execution"):
                _get_path(post_normalized, "buy").pop("execution", None)
    for allowed_diffs, execution_key in (
        (allowed_buy_execution_additional_diffs, "additional"),
        (allowed_buy_execution_cycle_diffs, "cycle"),
    ):
        path = f"buy.execution.{execution_key}"
        if allowed_diffs and _path_exists(post_normalized, path):
            if _path_exists(pre_normalized, path):
                _get_path(post_normalized, "buy.execution")[execution_key] = deepcopy(
                    _get_path(pre_normalized, path)
                )
            elif _path_exists(post_normalized, "buy.execution"):
                _get_path(post_normalized, "buy.execution").pop(execution_key, None)
                if _get_path(post_normalized, "buy.execution") == {} and not _path_exists(pre_normalized, "buy.execution"):
                    _get_path(post_normalized, "buy").pop("execution", None)
    if (
        (
            allowed_buy_execution_base_diffs
            or allowed_buy_execution_repeat_diffs
            or allowed_buy_execution_additional_diffs
            or allowed_buy_execution_cycle_diffs
        )
        and _path_exists(pre_normalized, "buy.execution")
        and not isinstance(_get_path(pre_normalized, "buy.execution"), dict)
    ):
        _get_path(post_normalized, "buy")["execution"] = deepcopy(
            _get_path(pre_normalized, "buy.execution")
        )
    for diff in allowed_sell_method_diffs:
        path = str(diff.get("path") or "")
        if not _path_exists(post_normalized, path):
            continue
        parent_path, leaf = path.rsplit(".", 1)
        if _path_exists(pre_normalized, path):
            _get_path(post_normalized, parent_path)[leaf] = deepcopy(_get_path(pre_normalized, path))
        elif _path_exists(post_normalized, parent_path):
            _get_path(post_normalized, parent_path).pop(leaf, None)
            if _get_path(post_normalized, parent_path) == {} and not _path_exists(pre_normalized, parent_path):
                grandparent_path, parent_leaf = parent_path.rsplit(".", 1)
                if _path_exists(post_normalized, grandparent_path):
                    _get_path(post_normalized, grandparent_path).pop(parent_leaf, None)
    if allowed_profit_rate_signal_diffs and _path_exists(post_normalized, "sell.signals.profit_rate_sell"):
        if _path_exists(pre_normalized, "sell.signals.profit_rate_sell"):
            _get_path(post_normalized, "sell.signals")["profit_rate_sell"] = deepcopy(
                _get_path(pre_normalized, "sell.signals.profit_rate_sell")
            )
        elif _path_exists(post_normalized, "sell.signals"):
            _get_path(post_normalized, "sell.signals").pop("profit_rate_sell", None)
    if _path_exists(pre_normalized, "buy.groups[0].conditions") and _path_exists(post_normalized, "buy.groups[0].conditions"):
        _get_path(post_normalized, "buy.groups[0]")["conditions"] = deepcopy(_get_path(pre_normalized, "buy.groups[0].conditions"))
    if isinstance(post_normalized.get("sell", {}).get("signals"), dict) and allowed_sell_signal_diffs:
        for signal_key in allowed_extra_keys:
            post_normalized["sell"]["signals"].pop(signal_key, None)

    normalized_diff_paths = _diff_paths(pre_normalized, post_normalized)
    add_check("normalized_rules_deep_equal_outside_allowed_paths", not normalized_diff_paths)
    for path in normalized_diff_paths:
        add_unexpected(path, "non-allowed rules path changed")

    ok = all(check.get("ok") is True for check in checks) and not unexpected_changes
    return {
        "ok": ok,
        "checks": checks,
        "unexpected_changes": unexpected_changes,
    }
