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

from routine_instance_registry import routine_definition_by_id
from routine_package_contract import RULE_COMMIT_VALIDATOR_ROLE, load_routine_callable


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


def _routine_post_validation(
    gate: dict[str, Any],
    pre_rules: dict[str, Any],
    post_rules: dict[str, Any],
    final_diff: list[Any],
    safety_checks: dict[str, Any],
) -> dict[str, Any]:
    """Delegate rule-schema validation to the routine-owned validator."""
    definition_id = str(gate.get("routine_key") or "").strip()
    if not definition_id:
        raise RuntimeError("routine definition identity is required")
    definition = routine_definition_by_id(definition_id)
    if definition is None:
        raise RuntimeError("routine definition is unavailable")
    validator = load_routine_callable(definition, RULE_COMMIT_VALIDATOR_ROLE)
    result = validator(
        pre_rules=deepcopy(pre_rules),
        post_rules=deepcopy(post_rules),
        final_diff=deepcopy(final_diff),
        safety_checks=deepcopy(safety_checks),
    )
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise RuntimeError("routine rule validator result is invalid")
    return result


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
    if not safety_checks:
        return _blocked(
            "RULE_APPLY_COMMIT_BLOCKED",
            "commit preview safety checks are required",
            target_path,
        )
    for key, value in safety_checks.items():
        if value is not False:
            return _blocked(
                "RULE_APPLY_COMMIT_BLOCKED",
                f"unsafe commit preview safety check: {key}",
                target_path,
            )

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

    post_validation = _routine_post_validation(
        gate_copy,
        current_rules,
        post_rules,
        final_diff,
        safety_checks,
    )
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
