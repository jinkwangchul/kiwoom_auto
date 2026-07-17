# -*- coding: utf-8 -*-
"""Commit service for initializing execution runtime files.

By default this service creates order_executions.json and order_locks.json
only for explicit temp/test paths. Project runtime paths remain blocked unless
a ready file-init open policy and an extra project-runtime confirmation are
provided. It never creates directories, commits queues, calls SendOrder, or
connects to GUI/real execution.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from execution_runtime_file_init_commit_plan_orchestrator import ORCHESTRATOR_TYPE
from execution_runtime_reader import read_order_executions, read_order_locks


SERVICE_TYPE = "EXECUTION_RUNTIME_FILE_INIT_COMMIT_SERVICE"
STATUS_COMMITTED = "COMMITTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_ERROR = "ERROR"
STATUS_SKIPPED = "SKIPPED"
FILE_INIT_OPEN_POLICY_TYPE = "EXECUTION_RUNTIME_FILE_INIT_OPEN_POLICY"
FILE_INIT_OPEN_POLICY_READY = "READY_TO_OPEN_FILE_INIT"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _project_runtime_root() -> Path:
    return (Path(__file__).resolve().parent / "runtime").resolve(strict=False)


def _under_project_runtime(path: Path) -> bool:
    target = path.resolve(strict=False)
    try:
        target.relative_to(_project_runtime_root())
    except ValueError:
        return False
    return True


def _result(
    *,
    status: str,
    committed: bool = False,
    runtime_write: bool = False,
    created_files: list[str] | None = None,
    read_back_verified: bool = False,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "service_type": SERVICE_TYPE,
        "status": status,
        "committed": committed,
        "runtime_write": runtime_write,
        "created_files": list(created_files or []),
        "read_back_verified": read_back_verified,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _validate_orchestrator_result(value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(value, dict):
        return {}, _result(status=STATUS_INVALID, issues=["MALFORMED_FILE_INIT_COMMIT_PLAN_ORCHESTRATOR_RESULT"])

    orchestrator = _as_dict(value)
    if orchestrator.get("orchestrator_type") != ORCHESTRATOR_TYPE:
        return orchestrator, _result(status=STATUS_INVALID, issues=["INVALID_FILE_INIT_COMMIT_PLAN_ORCHESTRATOR_TYPE"])
    if orchestrator.get("status") != "READY":
        return orchestrator, _result(status=STATUS_BLOCKED, issues=_as_list(orchestrator.get("issues")) or ["FILE_INIT_COMMIT_PLAN_NOT_READY"])
    if orchestrator.get("init_commit_ready") is not True:
        return orchestrator, _result(status=STATUS_BLOCKED, issues=["INIT_COMMIT_READY_IS_NOT_TRUE"])
    if orchestrator.get("preview_only") is not True:
        return orchestrator, _result(status=STATUS_INVALID, issues=["PREVIEW_ONLY_REQUIRED"])
    if orchestrator.get("runtime_write") is not False:
        return orchestrator, _result(status=STATUS_INVALID, issues=["RUNTIME_WRITE_MUST_BE_FALSE"])

    validation = _as_dict(orchestrator.get("validation"))
    if validation.get("valid") is not True:
        return orchestrator, _result(status=STATUS_INVALID, issues=_as_list(validation.get("issues")) or ["FILE_INIT_COMMIT_PLAN_VALIDATION_FAILED"])

    commit_plan = _as_dict(orchestrator.get("commit_plan"))
    if commit_plan.get("status") != "READY" or commit_plan.get("init_commit_ready") is not True:
        return orchestrator, _result(status=STATUS_BLOCKED, issues=["FILE_INIT_COMMIT_PLAN_NOT_READY"])

    return orchestrator, None


def _targets_and_schemas(orchestrator: dict[str, Any]) -> tuple[Path, Path, dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    commit_plan = _as_dict(orchestrator.get("commit_plan"))
    targets = _as_dict(commit_plan.get("planned_targets"))
    schemas = _as_dict(commit_plan.get("planned_schemas"))

    order_executions_text = _text(targets.get("order_executions"))
    order_locks_text = _text(targets.get("order_locks"))
    if not order_executions_text:
        empty = Path()
        return empty, empty, {}, {}, _result(status=STATUS_INVALID, issues=["MISSING_TARGET_ORDER_EXECUTIONS"])
    if not order_locks_text:
        empty = Path()
        return empty, empty, {}, {}, _result(status=STATUS_INVALID, issues=["MISSING_TARGET_ORDER_LOCKS"])

    order_executions_schema = deepcopy(_as_dict(schemas.get("order_executions")))
    order_locks_schema = deepcopy(_as_dict(schemas.get("order_locks")))
    if not order_executions_schema:
        return Path(order_executions_text), Path(order_locks_text), {}, {}, _result(status=STATUS_INVALID, issues=["MISSING_SCHEMA_ORDER_EXECUTIONS"])
    if not order_locks_schema:
        return Path(order_executions_text), Path(order_locks_text), {}, {}, _result(status=STATUS_INVALID, issues=["MISSING_SCHEMA_ORDER_LOCKS"])

    return (
        Path(order_executions_text),
        Path(order_locks_text),
        order_executions_schema,
        order_locks_schema,
        None,
    )


def _project_runtime_open_issue(
    file_init_open_policy_result: Any,
    *,
    manual_project_runtime_file_init_commit_confirmed: bool,
) -> dict[str, Any] | None:
    if file_init_open_policy_result is None:
        return _result(status=STATUS_BLOCKED, issues=["PROJECT_RUNTIME_PATH_BLOCKED"])
    if not isinstance(file_init_open_policy_result, dict):
        return _result(status=STATUS_INVALID, issues=["MALFORMED_FILE_INIT_OPEN_POLICY_RESULT"])

    policy = _as_dict(file_init_open_policy_result)
    if policy.get("policy_type") != FILE_INIT_OPEN_POLICY_TYPE:
        return _result(status=STATUS_INVALID, issues=["INVALID_FILE_INIT_OPEN_POLICY_TYPE"])
    if policy.get("preview_only") is not True:
        return _result(status=STATUS_INVALID, issues=["FILE_INIT_OPEN_POLICY_PREVIEW_ONLY_REQUIRED"])
    if policy.get("runtime_write") is not False:
        return _result(status=STATUS_INVALID, issues=["FILE_INIT_OPEN_POLICY_RUNTIME_WRITE_MUST_BE_FALSE"])

    policy_status = policy.get("status")
    if policy_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=_as_list(policy.get("issues")) or ["FILE_INIT_OPEN_POLICY_INVALID"],
            warnings=_as_list(policy.get("warnings")),
        )
    if policy_status == STATUS_SKIPPED:
        return _result(
            status=STATUS_SKIPPED,
            issues=_as_list(policy.get("issues")),
            warnings=_as_list(policy.get("warnings")),
        )
    if policy_status != FILE_INIT_OPEN_POLICY_READY:
        return _result(
            status=STATUS_BLOCKED,
            issues=_as_list(policy.get("issues")) or ["FILE_INIT_OPEN_POLICY_NOT_READY"],
            warnings=_as_list(policy.get("warnings")),
        )
    if policy.get("file_init_allowed") is not True:
        return _result(status=STATUS_BLOCKED, issues=["FILE_INIT_OPEN_POLICY_NOT_ALLOWED"])
    if manual_project_runtime_file_init_commit_confirmed is not True:
        return _result(status=STATUS_BLOCKED, issues=["MANUAL_PROJECT_RUNTIME_FILE_INIT_COMMIT_CONFIRMATION_REQUIRED"])
    return None


def _target_paths_ok(
    order_executions_path: Path,
    order_locks_path: Path,
    *,
    file_init_open_policy_result: Any = None,
    manual_project_runtime_file_init_commit_confirmed: bool = False,
) -> dict[str, Any] | None:
    if _under_project_runtime(order_executions_path) or _under_project_runtime(order_locks_path):
        open_issue = _project_runtime_open_issue(
            file_init_open_policy_result,
            manual_project_runtime_file_init_commit_confirmed=manual_project_runtime_file_init_commit_confirmed,
        )
        if open_issue is not None:
            return open_issue
    if not order_executions_path.parent.exists() or not order_locks_path.parent.exists():
        return _result(status=STATUS_BLOCKED, issues=["PARENT_DIRECTORY_MISSING"])
    if order_executions_path.exists():
        executions = read_order_executions(order_executions_path)
        if executions.get("ok") is not True:
            return _result(
                status=STATUS_BLOCKED,
                issues=list(executions.get("issues") or ["ORDER_EXECUTIONS_FILE_INVALID"]),
            )
    if order_locks_path.exists():
        locks = read_order_locks(order_locks_path)
        if locks.get("ok") is not True:
            return _result(
                status=STATUS_BLOCKED,
                issues=list(locks.get("issues") or ["ORDER_LOCKS_FILE_INVALID"]),
            )
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


def _read_back_verified(order_executions_path: Path, order_locks_path: Path) -> bool:
    executions = read_order_executions(order_executions_path)
    locks = read_order_locks(order_locks_path)
    return executions.get("ok") is True and locks.get("ok") is True


def commit_execution_runtime_file_init_plan(
    file_init_commit_plan_orchestrator_result: Any,
    *,
    manual_runtime_file_init_commit_confirmed: bool = False,
    manual_temp_file_init_confirmed: bool = False,
    file_init_open_policy_result: Any = None,
    manual_project_runtime_file_init_commit_confirmed: bool = False,
) -> dict[str, Any]:
    """Create initial runtime JSON files for approved temp or project paths."""
    orchestrator, invalid_result = _validate_orchestrator_result(file_init_commit_plan_orchestrator_result)
    if invalid_result is not None:
        return invalid_result

    (
        order_executions_path,
        order_locks_path,
        order_executions_schema,
        order_locks_schema,
        invalid_targets,
    ) = _targets_and_schemas(orchestrator)
    if invalid_targets is not None:
        return invalid_targets

    project_runtime_target = _under_project_runtime(order_executions_path) or _under_project_runtime(order_locks_path)
    if manual_runtime_file_init_commit_confirmed is not True:
        return _result(
            status=STATUS_BLOCKED,
            issues=["MANUAL_FILE_INIT_COMMIT_CONFIRMATIONS_REQUIRED"],
        )
    if not project_runtime_target and manual_temp_file_init_confirmed is not True:
        return _result(
            status=STATUS_BLOCKED,
            issues=["MANUAL_FILE_INIT_COMMIT_CONFIRMATIONS_REQUIRED"],
        )

    target_issue = _target_paths_ok(
        order_executions_path,
        order_locks_path,
        file_init_open_policy_result=file_init_open_policy_result,
        manual_project_runtime_file_init_commit_confirmed=manual_project_runtime_file_init_commit_confirmed,
    )
    if target_issue is not None:
        return target_issue

    created_files: list[str] = []
    try:
        if not order_executions_path.exists():
            _write_json_atomic(order_executions_path, order_executions_schema)
            created_files.append(str(order_executions_path))
        if not order_locks_path.exists():
            _write_json_atomic(order_locks_path, order_locks_schema)
            created_files.append(str(order_locks_path))
    except Exception as exc:
        for created_file in created_files:
            try:
                Path(created_file).unlink()
            except OSError:
                pass
        return _result(
            status=STATUS_ERROR,
            created_files=created_files,
            issues=[f"FILE_INIT_COMMIT_FAILED: {exc}"],
        )

    verified = _read_back_verified(order_executions_path, order_locks_path)
    if not verified:
        return _result(
            status=STATUS_ERROR,
            runtime_write=True,
            created_files=created_files,
            issues=["READ_BACK_VERIFICATION_FAILED"],
        )

    return _result(
        status=STATUS_COMMITTED,
        committed=True,
        runtime_write=True,
        created_files=created_files,
        read_back_verified=True,
        warnings=_as_list(orchestrator.get("warnings")),
    )
