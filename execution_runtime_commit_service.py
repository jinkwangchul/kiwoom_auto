# -*- coding: utf-8 -*-
"""Manual-only execution runtime commit service for explicit paths.

This service commits validated execution runtime commit plans to already
existing JSON files. Project runtime paths remain blocked unless a ready real
commit readiness policy and an extra project-runtime confirmation are provided.
It never creates missing target files or directories.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from execution_runtime_commit_plan_orchestrator import ORCHESTRATOR_TYPE
from execution_runtime_commit_plan_validator import validate_execution_runtime_commit_plan_preview
from execution_runtime_reader import read_order_executions, read_order_locks


SERVICE_TYPE = "EXECUTION_RUNTIME_COMMIT_SERVICE"
STATUS_COMMITTED = "COMMITTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_ERROR = "ERROR"
REAL_COMMIT_POLICY_TYPE = "EXECUTION_RUNTIME_REAL_COMMIT_READINESS_POLICY"
REAL_COMMIT_POLICY_READY = "READY_TO_OPEN_RUNTIME_COMMIT"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    runtime_write: bool = False,
    committed: bool = False,
    execution_id: str | None = None,
    order_id: str | None = None,
    request_hash: str | None = None,
    lock_id: str | None = None,
    execution_record: dict[str, Any] | None = None,
    lock_record: dict[str, Any] | None = None,
    order_executions_path: str | None = None,
    order_locks_path: str | None = None,
    backup_paths: dict[str, Any] | None = None,
    read_back_verified: bool = False,
    rollback_attempted: bool = False,
    rollback_succeeded: bool = False,
    restored_from_backup: list[str] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "service_type": SERVICE_TYPE,
        "status": status,
        "runtime_write": runtime_write,
        "committed": committed,
        "execution_id": execution_id,
        "order_id": order_id,
        "request_hash": request_hash,
        "lock_id": lock_id,
        "execution_record": deepcopy(execution_record) if isinstance(execution_record, dict) else {},
        "lock_record": deepcopy(lock_record) if isinstance(lock_record, dict) else {},
        "order_executions_path": order_executions_path,
        "order_locks_path": order_locks_path,
        "backup_paths": deepcopy(backup_paths or {}),
        "read_back_verified": read_back_verified,
        "rollback_attempted": rollback_attempted,
        "rollback_succeeded": rollback_succeeded,
        "restored_from_backup": list(restored_from_backup or []),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _under_project_runtime(path: Path) -> bool:
    project_runtime = (Path(__file__).resolve().parent / "runtime").resolve()
    target = path.resolve(strict=False)
    try:
        target.relative_to(project_runtime)
    except ValueError:
        return False
    return True


def _validate_orchestrator_result(value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(value, dict):
        return {}, _result(status=STATUS_INVALID, issues=["MALFORMED_COMMIT_PLAN_ORCHESTRATOR_RESULT"])

    orchestrator = _as_dict(value)
    if orchestrator.get("orchestrator_type") != ORCHESTRATOR_TYPE:
        return orchestrator, _result(status=STATUS_INVALID, issues=["INVALID_COMMIT_PLAN_ORCHESTRATOR_TYPE"])
    if orchestrator.get("status") != "READY":
        return orchestrator, _result(status=STATUS_BLOCKED, issues=list(orchestrator.get("issues") or ["COMMIT_PLAN_NOT_READY"]))
    if orchestrator.get("commit_ready") is not True:
        return orchestrator, _result(status=STATUS_BLOCKED, issues=["COMMIT_READY_IS_NOT_TRUE"])
    if orchestrator.get("preview_only") is not True:
        return orchestrator, _result(status=STATUS_INVALID, issues=["PREVIEW_ONLY_REQUIRED"])
    if orchestrator.get("runtime_write") is not False:
        return orchestrator, _result(status=STATUS_INVALID, issues=["RUNTIME_WRITE_MUST_BE_FALSE"])

    commit_plan = _as_dict(orchestrator.get("commit_plan"))
    validation = _as_dict(orchestrator.get("validation"))
    if validation.get("valid") is not True:
        return orchestrator, _result(status=STATUS_INVALID, issues=list(validation.get("issues") or ["COMMIT_PLAN_VALIDATION_FAILED"]))

    fresh_validation = validate_execution_runtime_commit_plan_preview(commit_plan)
    if fresh_validation.get("valid") is not True:
        return orchestrator, _result(status=STATUS_INVALID, issues=list(fresh_validation.get("issues") or ["COMMIT_PLAN_VALIDATION_FAILED"]))

    if commit_plan.get("preview_only") is not True or commit_plan.get("runtime_write") is not False:
        return orchestrator, _result(status=STATUS_INVALID, issues=["INVALID_COMMIT_PLAN_PREVIEW_FLAGS"])

    return orchestrator, None


def _confirmations_ok(context: Any) -> bool:
    ctx = _as_dict(context)
    return (
        ctx.get("manual_execution_runtime_commit_confirmed") is True
        and ctx.get("manual_runtime_file_write_confirmed") is True
    )


def _project_runtime_open_issue(
    real_commit_readiness_policy_result: Any,
    *,
    manual_project_runtime_commit_confirmed: bool,
) -> dict[str, Any] | None:
    if real_commit_readiness_policy_result is None:
        return _result(status=STATUS_BLOCKED, issues=["PROJECT_RUNTIME_PATH_BLOCKED"])
    if not isinstance(real_commit_readiness_policy_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_REAL_COMMIT_READINESS_POLICY_RESULT"])

    policy = _as_dict(real_commit_readiness_policy_result)
    if policy.get("policy_type") != REAL_COMMIT_POLICY_TYPE:
        return _result(status=STATUS_INVALID, issues=["INVALID_REAL_COMMIT_READINESS_POLICY_TYPE"])
    if policy.get("preview_only") is not True:
        return _result(status=STATUS_INVALID, issues=["REAL_COMMIT_POLICY_PREVIEW_ONLY_REQUIRED"])
    if policy.get("runtime_write") is not False:
        return _result(status=STATUS_INVALID, issues=["REAL_COMMIT_POLICY_RUNTIME_WRITE_MUST_BE_FALSE"])

    policy_status = policy.get("status")
    if policy_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(policy.get("issues")) or ["REAL_COMMIT_READINESS_POLICY_INVALID"],
            warnings=_as_list(policy.get("warnings")),
        )
    if policy_status != REAL_COMMIT_POLICY_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(policy.get("issues")) or ["REAL_COMMIT_READINESS_POLICY_NOT_READY"],
            warnings=_as_list(policy.get("warnings")),
        )
    if policy.get("runtime_commit_allowed") is not True:
        return _result(status=STATUS_BLOCKED, issues=["REAL_COMMIT_READINESS_POLICY_NOT_ALLOWED"])
    if manual_project_runtime_commit_confirmed is not True:
        return _result(status=STATUS_BLOCKED, issues=["MANUAL_PROJECT_RUNTIME_COMMIT_CONFIRMATION_REQUIRED"])
    return None


def _target_paths_ok(
    order_executions_path: Path,
    order_locks_path: Path,
    *,
    real_commit_readiness_policy_result: Any = None,
    manual_project_runtime_commit_confirmed: bool = False,
) -> dict[str, Any] | None:
    if _under_project_runtime(order_executions_path) or _under_project_runtime(order_locks_path):
        open_issue = _project_runtime_open_issue(
            real_commit_readiness_policy_result,
            manual_project_runtime_commit_confirmed=manual_project_runtime_commit_confirmed,
        )
        if open_issue is not None:
            open_issue["order_executions_path"] = str(order_executions_path)
            open_issue["order_locks_path"] = str(order_locks_path)
            return open_issue
    if not order_executions_path.exists():
        return _result(
            status=STATUS_BLOCKED,
            order_executions_path=str(order_executions_path),
            order_locks_path=str(order_locks_path),
            issues=["MISSING_ORDER_EXECUTIONS_FILE"],
        )
    if not order_locks_path.exists():
        return _result(
            status=STATUS_BLOCKED,
            order_executions_path=str(order_executions_path),
            order_locks_path=str(order_locks_path),
            issues=["MISSING_ORDER_LOCKS_FILE"],
        )
    return None


def _duplicate_issue(items: Any, *, fields: tuple[str, ...], record: dict[str, Any]) -> str | None:
    if not isinstance(items, list):
        return "MALFORMED_EXISTING_ITEMS"
    for field in fields:
        value = _text(record.get(field))
        if not value:
            continue
        for item in items:
            if isinstance(item, dict) and _text(item.get(field)) == value:
                return f"DUPLICATE_{field.upper()}"
    return None


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
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


def _make_backup(path: Path) -> str:
    backup_path = str(path) + ".bak"
    shutil.copy2(path, backup_path)
    return backup_path


def _restore_backups(backup_paths: dict[str, Any]) -> tuple[bool, list[str]]:
    restored: list[str] = []
    for key in ("order_executions", "order_locks"):
        backup_path = _text(backup_paths.get(key))
        if not backup_path:
            continue
        source = Path(backup_path)
        if not source.exists():
            return False, restored
        target_name = key + ".json"
        target = source.with_name(target_name)
        try:
            shutil.copy2(source, target)
        except Exception:
            return False, restored
        restored.append(str(target))
    return True, restored


def _read_back_contains(path: Path, *, field: str, record: dict[str, Any], keys: tuple[str, ...]) -> bool:
    read_result = read_order_executions(path) if field == "executions" else read_order_locks(path)
    if read_result.get("ok") is not True:
        return False
    items = _as_dict(read_result.get("data")).get(field)
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        if all(_text(item.get(key)) == _text(record.get(key)) for key in keys):
            return True
    return False


def commit_execution_runtime_plan(
    commit_plan_orchestrator_result: Any,
    order_executions_path: str | Path,
    order_locks_path: str | Path,
    *,
    context: Any = None,
    backup: bool = True,
    real_commit_readiness_policy_result: Any = None,
    manual_project_runtime_commit_confirmed: bool = False,
) -> dict[str, Any]:
    """Append execution and lock records to approved explicit target files."""
    order_executions_target = Path(order_executions_path)
    order_locks_target = Path(order_locks_path)

    orchestrator, invalid_result = _validate_orchestrator_result(commit_plan_orchestrator_result)
    if invalid_result is not None:
        invalid_result["order_executions_path"] = str(order_executions_target)
        invalid_result["order_locks_path"] = str(order_locks_target)
        return invalid_result

    if not _confirmations_ok(context):
        return _result(
            status=STATUS_BLOCKED,
            order_executions_path=str(order_executions_target),
            order_locks_path=str(order_locks_target),
            issues=["MANUAL_CONFIRMATIONS_REQUIRED"],
        )

    target_issue = _target_paths_ok(
        order_executions_target,
        order_locks_target,
        real_commit_readiness_policy_result=real_commit_readiness_policy_result,
        manual_project_runtime_commit_confirmed=manual_project_runtime_commit_confirmed,
    )
    if target_issue is not None:
        return target_issue

    executions_read = read_order_executions(order_executions_target)
    locks_read = read_order_locks(order_locks_target)
    if executions_read.get("ok") is not True:
        return _result(
            status=STATUS_BLOCKED,
            order_executions_path=str(order_executions_target),
            order_locks_path=str(order_locks_target),
            issues=list(executions_read.get("issues") or ["ORDER_EXECUTIONS_SCHEMA_INVALID"]),
        )
    if locks_read.get("ok") is not True:
        return _result(
            status=STATUS_BLOCKED,
            order_executions_path=str(order_executions_target),
            order_locks_path=str(order_locks_target),
            issues=list(locks_read.get("issues") or ["ORDER_LOCKS_SCHEMA_INVALID"]),
        )

    commit_plan = _as_dict(orchestrator.get("commit_plan"))
    planned_records = _as_dict(commit_plan.get("planned_records"))
    execution_record = deepcopy(_as_dict(planned_records.get("execution")))
    lock_record = deepcopy(_as_dict(planned_records.get("lock")))
    executions_data = deepcopy(_as_dict(executions_read.get("data")))
    locks_data = deepcopy(_as_dict(locks_read.get("data")))

    execution_duplicate = _duplicate_issue(
        executions_data.get("executions"),
        fields=("execution_id", "request_hash", "order_id"),
        record=execution_record,
    )
    if execution_duplicate:
        return _result(
            status=STATUS_BLOCKED,
            order_executions_path=str(order_executions_target),
            order_locks_path=str(order_locks_target),
            issues=[execution_duplicate],
        )

    lock_duplicate = _duplicate_issue(
        locks_data.get("locks"),
        fields=("lock_id", "request_hash", "order_id"),
        record=lock_record,
    )
    if lock_duplicate:
        return _result(
            status=STATUS_BLOCKED,
            order_executions_path=str(order_executions_target),
            order_locks_path=str(order_locks_target),
            issues=[lock_duplicate],
        )

    backup_paths: dict[str, Any] = {}
    try:
        if backup:
            backup_paths = {
                "order_executions": _make_backup(order_executions_target),
                "order_locks": _make_backup(order_locks_target),
            }

        executions_data["executions"] = list(executions_data.get("executions") or []) + [execution_record]
        locks_data["locks"] = list(locks_data.get("locks") or []) + [lock_record]
        _write_json_atomic(order_executions_target, executions_data)
        _write_json_atomic(order_locks_target, locks_data)
    except Exception as exc:
        rollback_attempted = bool(backup_paths)
        rollback_succeeded = False
        restored_from_backup: list[str] = []
        rollback_issues: list[str] = []
        if rollback_attempted:
            rollback_succeeded, restored_from_backup = _restore_backups(backup_paths)
            if not rollback_succeeded:
                rollback_issues.append("ROLLBACK_FAILED")
        return _result(
            status=STATUS_ERROR,
            order_executions_path=str(order_executions_target),
            order_locks_path=str(order_locks_target),
            backup_paths=backup_paths,
            rollback_attempted=rollback_attempted,
            rollback_succeeded=rollback_succeeded,
            restored_from_backup=restored_from_backup,
            issues=[f"COMMIT_WRITE_FAILED: {exc}"] + rollback_issues,
        )

    read_back_verified = (
        _read_back_contains(
            order_executions_target,
            field="executions",
            record=execution_record,
            keys=("execution_id", "order_id", "request_hash"),
        )
        and _read_back_contains(
            order_locks_target,
            field="locks",
            record=lock_record,
            keys=("lock_id", "order_id", "request_hash"),
        )
    )
    if not read_back_verified:
        return _result(
            status=STATUS_ERROR,
            runtime_write=True,
            committed=False,
            execution_id=_text(execution_record.get("execution_id")),
            order_id=_text(execution_record.get("order_id")),
            request_hash=_text(execution_record.get("request_hash")),
            lock_id=_text(lock_record.get("lock_id")),
            execution_record=execution_record,
            lock_record=lock_record,
            order_executions_path=str(order_executions_target),
            order_locks_path=str(order_locks_target),
            backup_paths=backup_paths,
            issues=["READ_BACK_VERIFICATION_FAILED"],
        )

    return _result(
        status=STATUS_COMMITTED,
        runtime_write=True,
        committed=True,
        execution_id=_text(execution_record.get("execution_id")),
        order_id=_text(execution_record.get("order_id")),
        request_hash=_text(execution_record.get("request_hash")),
        lock_id=_text(lock_record.get("lock_id")),
        execution_record=execution_record,
        lock_record=lock_record,
        order_executions_path=str(order_executions_target),
        order_locks_path=str(order_locks_target),
        backup_paths=backup_paths,
        read_back_verified=True,
        warnings=_as_list(orchestrator.get("warnings")),
    )
