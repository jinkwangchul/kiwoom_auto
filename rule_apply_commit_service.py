"""Commit approved rule apply previews to an explicit rules.json path.

This module is the file-write executor only. It does not rebuild approval,
patch, apply, or commit-gate results, and it does not connect to any engine.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _apply_preview_hash_payload(apply_preview: dict[str, Any]) -> dict[str, Any]:
    preview = _as_dict(apply_preview)
    return {
        "applied_rules_preview": deepcopy(_as_dict(preview.get("applied_rules_preview"))),
        "applied_patches": deepcopy(_as_list(preview.get("applied_patches"))),
        "skipped_patches": deepcopy(_as_list(preview.get("skipped_patches"))),
        "summary": deepcopy(_as_dict(preview.get("summary"))),
    }


def _apply_preview_hash(apply_preview: dict[str, Any]) -> str:
    return _stable_hash(_apply_preview_hash_payload(apply_preview))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _blocked(stage: str, reason: str, rules_path: Any = None, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": stage,
        "committed": False,
        "rules_path": str(rules_path) if rules_path else None,
        "backup_path": None,
        "blocked_reasons": [reason],
        "warnings": warnings or [],
    }


def _load_rules(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "rules file does not exist"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"failed to read rules JSON: {exc}"
    if not isinstance(data, dict):
        return None, "rules JSON root must be a dict"
    return data, None


def _create_backup(rules_path: Path, pre_file_sha256: str) -> Path:
    backup_dir = rules_path.parent / "backups" / "rules"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"rules_{_now_stamp()}_{pre_file_sha256[:8]}.json"
    if backup_path.exists():
        suffix = 1
        while True:
            candidate = backup_dir / f"rules_{_now_stamp()}_{pre_file_sha256[:8]}_{suffix:04d}.json"
            if not candidate.exists():
                backup_path = candidate
                break
            suffix += 1
    shutil.copy2(rules_path, backup_path)
    return backup_path


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _rules_path_guard_error(rules_path: Path, context: dict[str, Any]) -> str | None:
    if rules_path.name != "rules.json":
        return "rules file name must be rules.json"
    allowed_rules_path = context.get("allowed_rules_path")
    if not allowed_rules_path:
        return "allowed_rules_path is required"
    try:
        if rules_path.resolve() != Path(allowed_rules_path).resolve():
            return "rules path is not allowed"
    except OSError as exc:
        return f"failed to resolve rules path guard: {exc}"
    return None


def _rollback_blocked(
    reason: str,
    rules_path: Any = None,
    backup_path: Any = None,
    rollback_safety_backup_path: Any = None,
    warnings: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "ok": False,
        "stage": "RULE_ROLLBACK_BLOCKED",
        "rollback_completed": False,
        "rules_path": str(rules_path) if rules_path else None,
        "backup_path": str(backup_path) if backup_path else None,
        "rollback_safety_backup_path": (
            str(rollback_safety_backup_path) if rollback_safety_backup_path else None
        ),
        "blocked_reasons": [reason],
        "warnings": warnings or [],
    }
    if extra:
        result.update(extra)
    return result


def _create_rollback_safety_backup(rules_path: Path, pre_rollback_file_sha256: str) -> Path:
    backup_dir = rules_path.parent / "backups" / "rollback_safety"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (
        f"rules_rollback_safety_{_now_stamp()}_{pre_rollback_file_sha256[:8]}.json"
    )
    if backup_path.exists():
        suffix = 1
        while True:
            candidate = backup_dir / (
                f"rules_rollback_safety_{_now_stamp()}_{pre_rollback_file_sha256[:8]}_{suffix:04d}.json"
            )
            if not candidate.exists():
                backup_path = candidate
                break
            suffix += 1
    shutil.copy2(rules_path, backup_path)
    return backup_path


def restore_rules_from_backup(
    rules_path: str | Path,
    backup_path: str | Path,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Restore an explicit rules path from an explicit backup path."""
    if not rules_path:
        return _rollback_blocked("rules_path is required")
    if not backup_path:
        return _rollback_blocked("backup_path is required", rules_path)

    target_path = Path(rules_path)
    source_path = Path(backup_path)
    context_copy = deepcopy(context) if isinstance(context, dict) else {}
    warnings: list[str] = []

    guard_error = _rules_path_guard_error(target_path, context_copy)
    if guard_error:
        return _rollback_blocked(guard_error, target_path, source_path)

    if not target_path.exists():
        return _rollback_blocked("rules file does not exist", target_path, source_path)
    if not source_path.exists():
        return _rollback_blocked("backup file does not exist", target_path, source_path)
    try:
        if target_path.resolve() == source_path.resolve():
            return _rollback_blocked("rules_path and backup_path must be different", target_path, source_path)
    except OSError as exc:
        return _rollback_blocked(f"failed to resolve rollback paths: {exc}", target_path, source_path)

    pre_rollback_file_sha256 = _file_sha256(target_path)
    expected_current_file_sha256 = context_copy.get("expected_current_file_sha256")
    if not isinstance(expected_current_file_sha256, str) or not expected_current_file_sha256:
        return _rollback_blocked(
            "expected_current_file_sha256 is required",
            target_path,
            source_path,
            extra={"pre_rollback_file_sha256": pre_rollback_file_sha256},
        )
    if expected_current_file_sha256 != pre_rollback_file_sha256:
        return _rollback_blocked(
            "expected current file SHA256 mismatch",
            target_path,
            source_path,
            extra={"pre_rollback_file_sha256": pre_rollback_file_sha256},
        )

    current_rules, current_load_error = _load_rules(target_path)
    if current_load_error:
        return _rollback_blocked(
            current_load_error,
            target_path,
            source_path,
            extra={"pre_rollback_file_sha256": pre_rollback_file_sha256},
        )
    assert current_rules is not None
    pre_rollback_rules_hash = _stable_hash(current_rules)

    backup_rules, backup_load_error = _load_rules(source_path)
    if backup_load_error:
        return _rollback_blocked(
            f"failed to load backup rules: {backup_load_error}",
            target_path,
            source_path,
            extra={
                "pre_rollback_file_sha256": pre_rollback_file_sha256,
                "pre_rollback_rules_hash": pre_rollback_rules_hash,
            },
        )
    assert backup_rules is not None
    backup_file_sha256 = _file_sha256(source_path)
    backup_rules_hash = _stable_hash(backup_rules)

    rollback_safety_backup_path: Path | None = None
    try:
        rollback_safety_backup_path = _create_rollback_safety_backup(target_path, pre_rollback_file_sha256)
    except Exception as exc:
        return _rollback_blocked(
            f"failed to create rollback safety backup: {exc}",
            target_path,
            source_path,
            extra={
                "pre_rollback_file_sha256": pre_rollback_file_sha256,
                "backup_file_sha256": backup_file_sha256,
                "pre_rollback_rules_hash": pre_rollback_rules_hash,
                "backup_rules_hash": backup_rules_hash,
            },
        )

    try:
        _write_json_atomic(target_path, backup_rules)
    except Exception as exc:
        return _rollback_blocked(
            f"failed to write rollback rules atomically: {exc}",
            target_path,
            source_path,
            rollback_safety_backup_path,
            warnings,
            {
                "pre_rollback_file_sha256": pre_rollback_file_sha256,
                "backup_file_sha256": backup_file_sha256,
                "pre_rollback_rules_hash": pre_rollback_rules_hash,
                "backup_rules_hash": backup_rules_hash,
            },
        )

    post_rules, post_load_error = _load_rules(target_path)
    if post_load_error:
        return _rollback_blocked(
            f"failed to reload restored rules: {post_load_error}",
            target_path,
            source_path,
            rollback_safety_backup_path,
            warnings,
            {
                "write_completed": True,
                "manual_restore_required": True,
                "pre_rollback_file_sha256": pre_rollback_file_sha256,
                "backup_file_sha256": backup_file_sha256,
                "pre_rollback_rules_hash": pre_rollback_rules_hash,
                "backup_rules_hash": backup_rules_hash,
            },
        )
    assert post_rules is not None

    post_rollback_file_sha256 = _file_sha256(target_path)
    post_rollback_rules_hash = _stable_hash(post_rules)
    if backup_rules_hash != post_rollback_rules_hash:
        return _rollback_blocked(
            "post rollback hash mismatch",
            target_path,
            source_path,
            rollback_safety_backup_path,
            warnings,
            {
                "write_completed": True,
                "manual_restore_required": True,
                "pre_rollback_file_sha256": pre_rollback_file_sha256,
                "post_rollback_file_sha256": post_rollback_file_sha256,
                "backup_file_sha256": backup_file_sha256,
                "pre_rollback_rules_hash": pre_rollback_rules_hash,
                "post_rollback_rules_hash": post_rollback_rules_hash,
                "backup_rules_hash": backup_rules_hash,
            },
        )

    return {
        "ok": True,
        "stage": "RULE_ROLLBACK",
        "rollback_completed": True,
        "rules_path": str(target_path),
        "backup_path": str(source_path),
        "rollback_safety_backup_path": str(rollback_safety_backup_path),
        "pre_rollback_file_sha256": pre_rollback_file_sha256,
        "post_rollback_file_sha256": post_rollback_file_sha256,
        "backup_file_sha256": backup_file_sha256,
        "pre_rollback_rules_hash": pre_rollback_rules_hash,
        "post_rollback_rules_hash": post_rollback_rules_hash,
        "backup_rules_hash": backup_rules_hash,
        "warnings": warnings,
    }


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


def _post_validation(
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
    allowed_sell_signal_path = "sell.signals.ui_condition_c_macd_sell"
    allowed_sell_signal_key = "ui_condition_c_macd_sell"
    allowed_sell_signal_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "add_signal"
        and diff.get("path") == allowed_sell_signal_path
    ]
    allowed_bar_minutes_diffs = [
        diff
        for diff in final_diff
        if isinstance(diff, dict)
        and diff.get("operation") == "set_value"
        and diff.get("path") == "bar.bar_minutes"
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
            else:
                add_check("final_diff_buy_execution_policy_path_allowed", False, path)
                add_unexpected(path or "<missing>", "unsupported buy.execution policy path")
        if operation == "add_signal":
            path = str(diff.get("path") or "")
            signal_exists = _path_exists(post_rules, path)
            signal = _get_path(post_rules, path) if signal_exists else None
            add_check("final_diff_sell_signal_exists", signal_exists)
            add_check(
                "final_diff_sell_signal_disabled",
                isinstance(signal, dict) and signal.get("enabled") is False,
            )
            add_check("final_diff_sell_macd_preserved", _path_exists(post_rules, "sell.signals.macd_sell"))

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
                unchanged = post_signals.get(key) == pre_signal
                add_check(f"existing_sell_signal_unchanged:{key}", unchanged)
                if not unchanged:
                    add_unexpected(f"sell.signals.{key}", "existing sell signal changed")

        extra_signal_keys = set(post_signals) - set(pre_signals)
        allowed_extra_keys = {allowed_sell_signal_key} if allowed_sell_signal_diffs else set()
        disallowed_extra_keys = extra_signal_keys - allowed_extra_keys
        add_check("sell_extra_signals_only_allowed_candidate", not disallowed_extra_keys)
        for key in sorted(disallowed_extra_keys):
            add_unexpected(f"sell.signals.{key}", "unapproved new sell signal added")

        if allowed_sell_signal_diffs:
            signal = post_signals.get(allowed_sell_signal_key)
            add_check("allowed_sell_signal_exists", isinstance(signal, dict))
            if not isinstance(signal, dict):
                add_unexpected(allowed_sell_signal_path, "final_diff sell signal missing from post rules")
            else:
                enabled_false = signal.get("enabled") is False
                no_preview_candidate = "preview_candidate" not in signal
                add_check("allowed_sell_signal_disabled", enabled_false)
                add_check("allowed_sell_signal_not_preview_candidate", no_preview_candidate)
                if not enabled_false:
                    add_unexpected(allowed_sell_signal_path, "allowed sell signal is not disabled")
                if not no_preview_candidate:
                    add_unexpected(allowed_sell_signal_path, "allowed sell signal contains preview_candidate")
    else:
        add_check("sell_signals_dict", False, "sell.signals missing or not a dict")
        add_unexpected("sell.signals", "sell signals structure changed")

    pre_normalized = deepcopy(pre_rules)
    post_normalized = deepcopy(post_rules)
    if allowed_bar_minutes_diffs and _path_exists(pre_normalized, "bar.bar_minutes") and _path_exists(post_normalized, "bar.bar_minutes"):
        _get_path(post_normalized, "bar")["bar_minutes"] = deepcopy(_get_path(pre_normalized, "bar.bar_minutes"))
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
    if _path_exists(pre_normalized, "buy.groups[0].conditions") and _path_exists(post_normalized, "buy.groups[0].conditions"):
        _get_path(post_normalized, "buy.groups[0]")["conditions"] = deepcopy(_get_path(pre_normalized, "buy.groups[0].conditions"))
    if isinstance(post_normalized.get("sell", {}).get("signals"), dict) and allowed_sell_signal_diffs:
        post_normalized["sell"]["signals"].pop(allowed_sell_signal_key, None)

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


def commit_approved_rule_patch_to_rules(
    rules_path: str | Path,
    apply_preview: dict[str, Any],
    commit_gate_result: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write an already-reviewed applied_rules_preview to an explicit rules path."""
    if not rules_path:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "rules_path is required")

    target_path = Path(rules_path)
    apply_preview_copy = deepcopy(apply_preview) if isinstance(apply_preview, dict) else {}
    gate_copy = deepcopy(commit_gate_result) if isinstance(commit_gate_result, dict) else {}
    context_copy = deepcopy(context) if isinstance(context, dict) else {}
    warnings: list[str] = []

    guard_error = _rules_path_guard_error(target_path, context_copy)
    if guard_error:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", guard_error, target_path)

    if gate_copy.get("commit_allowed") is not True:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "commit gate is not allowed", target_path)

    applied_rules_preview = apply_preview_copy.get("applied_rules_preview")
    if not isinstance(applied_rules_preview, dict):
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "apply_preview.applied_rules_preview is required", target_path)

    current_rules, load_error = _load_rules(target_path)
    if load_error:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", load_error, target_path)
    assert current_rules is not None

    pre_file_sha256 = _file_sha256(target_path)
    pre_rules_hash = _stable_hash(current_rules)
    expected_file_sha256 = context_copy.get("expected_file_sha256")
    expected_rules_hash = context_copy.get("expected_rules_hash")
    if expected_file_sha256 != pre_file_sha256:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "expected file SHA256 mismatch", target_path)
    if expected_rules_hash != pre_rules_hash:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "expected rules stable hash mismatch", target_path)

    gate_hash = gate_copy.get("rules_hash_check", {}).get("current_rules_hash")
    if gate_hash != pre_rules_hash:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "commit gate rules hash mismatch", target_path)

    commit_preview = gate_copy.get("commit_preview", {}) if isinstance(gate_copy.get("commit_preview"), dict) else {}
    safety_checks = commit_preview.get("safety_checks", {}) if isinstance(commit_preview.get("safety_checks"), dict) else {}
    for key in ("rules_json_write", "engine_connected", "buy_groups_replace", "macd_sell_replace"):
        if safety_checks.get(key) is not False:
            return _blocked("RULE_APPLY_COMMIT_BLOCKED", f"unsafe commit preview safety check: {key}", target_path)

    final_diff = commit_preview.get("final_diff")
    if not isinstance(final_diff, list) or not final_diff:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "commit preview final_diff is required", target_path)

    gate_apply_preview_hash = gate_copy.get("apply_preview_hash")
    commit_preview_apply_hash = commit_preview.get("apply_preview_hash")
    if not isinstance(gate_apply_preview_hash, str) or not gate_apply_preview_hash:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "apply preview hash is required", target_path)
    if not isinstance(commit_preview_apply_hash, str) or not commit_preview_apply_hash:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "apply preview hash is required", target_path)
    if gate_apply_preview_hash != commit_preview_apply_hash:
        return _blocked(
            "RULE_APPLY_COMMIT_BLOCKED",
            "apply preview hash mismatch between commit gate and commit preview",
            target_path,
        )
    if gate_copy.get("apply_preview_hash_algorithm") != "stable_json_sha256":
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "apply preview hash algorithm is invalid", target_path)
    if commit_preview.get("apply_preview_hash_algorithm") != "stable_json_sha256":
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", "apply preview hash algorithm is invalid", target_path)
    current_apply_preview_hash = _apply_preview_hash(apply_preview_copy)
    if current_apply_preview_hash != gate_apply_preview_hash:
        return _blocked(
            "RULE_APPLY_COMMIT_BLOCKED",
            "apply preview changed after commit gate; rerun commit preview and gate",
            target_path,
        )

    commit_id = f"{_now_stamp()}_{pre_file_sha256[:8]}"
    backup_path: Path | None = None
    try:
        backup_path = _create_backup(target_path, pre_file_sha256)
    except Exception as exc:
        return _blocked("RULE_APPLY_COMMIT_BLOCKED", f"failed to create rules backup: {exc}", target_path)

    try:
        _write_json_atomic(target_path, applied_rules_preview)
    except Exception as exc:
        return {
            "ok": False,
            "stage": "RULE_APPLY_COMMIT_BLOCKED",
            "committed": False,
            "rules_path": str(target_path),
            "backup_path": str(backup_path),
            "blocked_reasons": [f"failed to write rules atomically: {exc}"],
            "warnings": warnings,
        }

    post_rules, post_load_error = _load_rules(target_path)
    if post_load_error:
        return {
            "ok": False,
            "stage": "RULE_APPLY_COMMIT_BLOCKED",
            "committed": False,
            "rules_path": str(target_path),
            "backup_path": str(backup_path),
            "blocked_reasons": [f"failed to reload committed rules: {post_load_error}"],
            "warnings": warnings,
        }
    assert post_rules is not None

    post_file_sha256 = _file_sha256(target_path)
    post_rules_hash = _stable_hash(post_rules)
    if post_file_sha256 == pre_file_sha256 or post_rules_hash == pre_rules_hash:
        return {
            "ok": False,
            "stage": "RULE_APPLY_COMMIT_BLOCKED",
            "committed": False,
            "rules_path": str(target_path),
            "backup_path": str(backup_path),
            "pre_file_sha256": pre_file_sha256,
            "post_file_sha256": post_file_sha256,
            "pre_rules_hash": pre_rules_hash,
            "post_rules_hash": post_rules_hash,
            "blocked_reasons": ["committed rules did not change"],
            "warnings": warnings,
        }

    post_validation = _post_validation(current_rules, post_rules, final_diff, safety_checks)
    if post_validation.get("ok") is not True:
        return {
            "ok": False,
            "stage": "RULE_APPLY_COMMIT_BLOCKED",
            "committed": False,
            "write_completed": True,
            "post_validation_ok": False,
            "commit_accepted": False,
            "manual_restore_required": True,
            "rollback_attempted": False,
            "commit_id": commit_id,
            "rules_path": str(target_path),
            "backup_path": str(backup_path),
            "pre_file_sha256": pre_file_sha256,
            "post_file_sha256": post_file_sha256,
            "pre_rules_hash": pre_rules_hash,
            "post_rules_hash": post_rules_hash,
            "apply_preview_hash": current_apply_preview_hash,
            "apply_preview_hash_algorithm": "stable_json_sha256",
            "applied_patches": deepcopy(apply_preview_copy.get("applied_patches", [])),
            "post_validation": post_validation,
            "blocked_reasons": ["post validation deep compare failed"],
            "warnings": warnings,
        }

    return {
        "ok": True,
        "stage": "RULE_APPLY_COMMIT",
        "committed": True,
        "commit_id": commit_id,
        "rules_path": str(target_path),
        "backup_path": str(backup_path),
        "pre_file_sha256": pre_file_sha256,
        "post_file_sha256": post_file_sha256,
        "pre_rules_hash": pre_rules_hash,
        "post_rules_hash": post_rules_hash,
        "apply_preview_hash": current_apply_preview_hash,
        "apply_preview_hash_algorithm": "stable_json_sha256",
        "applied_patches": deepcopy(apply_preview_copy.get("applied_patches", [])),
        "skipped_patches": deepcopy(apply_preview_copy.get("skipped_patches", [])),
        "post_validation": post_validation,
        "warnings": warnings,
    }
