# -*- coding: utf-8 -*-
"""Atomic, broker-isolated reset of project-local user operation data."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Callable
from uuid import uuid4

from startup_runtime_initializer import (
    STATUS_INITIALIZED,
    initialize_pristine_startup_runtime,
    startup_runtime_paths,
)


PROJECT_ROOT = Path(__file__).resolve().parent

RESET_DIRECTORIES = (
    "groups",
    "routine_instances",
    "stocks",
    "archived_stocks",
    "assignment_episodes",
    "performance_ledger",
    "migration_manifests",
    "artifacts",
    "reports",
    "logs",
    "runtime",
)
DELETE_FILES = ("invalid_items.log",)
ROUTINE_DERIVED_FILES = ("approval_session.json",)
ROUTINE_DERIVED_DIRS = ("reports",)
RESET_FILES = ("operation_policy.json", "global_schedule.json")
PRESERVE_PATHS = (
    "routines",
    "engines",
    "runtime/stock_library.json",
    "runtime/stock_library_meta.json",
    "stock_library.json",
    "screen_registry.json",
    "기초종목.txt",
    "PROJECT_CHANGELOG.txt",
)
PRESERVED_RUNTIME_FILES = ("stock_library.json", "stock_library_meta.json")
TRANSACTION_ROOT_NAME = ".factory_reset_transactions"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

QuiesceCallback = Callable[[], object]
ResumeCallback = Callable[[object], None]


def factory_reset_manifest() -> dict[str, tuple[str, ...]]:
    return {
        "DELETE_CONTENTS": RESET_DIRECTORIES,
        "DELETE_FILES": DELETE_FILES,
        "DELETE_ROUTINE_DERIVED_FILES": ROUTINE_DERIVED_FILES,
        "DELETE_ROUTINE_DERIVED_DIRS": ROUTINE_DERIVED_DIRS,
        "RESET": RESET_FILES,
        "PRESERVE": PRESERVE_PATHS,
    }


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.stat(), "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return False


def _project_child(root: Path, relative: str) -> Path:
    resolved_root = root.resolve(strict=True)
    candidate = root / relative
    if candidate.exists() and _is_link_or_reparse(candidate):
        raise RuntimeError(f"심볼릭 링크 경로는 초기화할 수 없습니다: {relative}")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"허용되지 않은 초기화 경로입니다: {relative}") from exc
    return candidate


def _safe_remove(path: Path, *, allowed_root: Path) -> None:
    resolved_root = allowed_root.resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"초기화 transaction 밖의 경로는 제거할 수 없습니다: {path}") from exc
    if not path.exists():
        return
    if _is_link_or_reparse(path):
        raise RuntimeError(f"심볼릭 링크는 제거할 수 없습니다: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _json_list_count(path: Path, field: str) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    values = payload.get(field) if isinstance(payload, dict) else None
    return len(values) if isinstance(values, list) else 0


def _directory_record_count(root: Path, marker: str) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.iterdir() if path.is_dir() and (path / marker).is_file())


def _recursive_file_count(root: Path, *, excluded_names: set[str] | None = None) -> int:
    if not root.is_dir():
        return 0
    excluded = excluded_names or set()
    return sum(1 for path in root.rglob("*") if path.is_file() and path.name not in excluded)


def build_factory_reset_preview(project_root: str | Path = PROJECT_ROOT) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=True)
    groups_root = _project_child(root, "groups")
    instances_root = _project_child(root, "routine_instances")
    stocks_root = _project_child(root, "stocks")
    episodes_root = _project_child(root, "assignment_episodes")
    ledger_root = _project_child(root, "performance_ledger")
    runtime_root = _project_child(root, "runtime")

    removed_assignment_episodes = sum(
        _json_list_count(path, "episodes") for path in episodes_root.glob("*/episodes.json")
    ) if episodes_root.is_dir() else 0
    removed_performance_events = sum(
        _json_list_count(path, "events") for path in ledger_root.glob("*/events.json")
    ) if ledger_root.is_dir() else 0
    return {
        "removed_groups": _directory_record_count(groups_root, "group.json"),
        "removed_instances": _directory_record_count(instances_root, "instance.json"),
        "removed_stocks": _directory_record_count(stocks_root, "config.json"),
        "removed_assignment_episodes": removed_assignment_episodes,
        "removed_performance_events": removed_performance_events,
        "removed_runtime_items": _recursive_file_count(
            runtime_root,
            excluded_names=set(PRESERVED_RUNTIME_FILES),
        ),
    }


def _result(
    success: bool,
    *,
    preview: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    initialized_at: str = "",
    rollback_complete: bool | None = None,
) -> dict[str, Any]:
    counts = dict(preview or {})
    return {
        "success": success,
        "initialized_at": initialized_at,
        "removed_groups": int(counts.get("removed_groups", 0)),
        "removed_instances": int(counts.get("removed_instances", 0)),
        "removed_stocks": int(counts.get("removed_stocks", 0)),
        "removed_assignment_episodes": int(counts.get("removed_assignment_episodes", 0)),
        "removed_performance_events": int(counts.get("removed_performance_events", 0)),
        "removed_runtime_items": int(counts.get("removed_runtime_items", 0)),
        "broker_orders_called": 0,
        "rollback_complete": rollback_complete,
        "issues": list(issues or []),
        "manifest": factory_reset_manifest(),
    }


def validate_factory_reset_safety(
    project_root: str | Path = PROJECT_ROOT,
    *,
    broker_connected: bool = False,
) -> dict[str, Any]:
    """Validate filesystem boundaries only; operation/account state never blocks reset."""
    del broker_connected
    try:
        root = Path(project_root).resolve(strict=True)
        for relative in RESET_DIRECTORIES + DELETE_FILES + RESET_FILES:
            _project_child(root, relative)
        routines_root = _project_child(root, "routines")
        if routines_root.exists() and (not routines_root.is_dir() or _is_link_or_reparse(routines_root)):
            raise RuntimeError("Routine Definition 저장 경로를 안전하게 확인할 수 없습니다.")
        transaction_root = _project_child(root, TRANSACTION_ROOT_NAME)
        if transaction_root.exists() and any(transaction_root.iterdir()):
            raise RuntimeError("완전초기화 transaction residue가 남아 있습니다.")
        preview = build_factory_reset_preview(root)
        return _result(True, preview=preview)
    except Exception as exc:
        return _result(False, issues=[str(exc)])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reset_targets(root: Path) -> list[tuple[str, Path]]:
    targets = [(relative, _project_child(root, relative)) for relative in RESET_DIRECTORIES]
    targets.extend((relative, _project_child(root, relative)) for relative in DELETE_FILES + RESET_FILES)
    routines_root = _project_child(root, "routines")
    if routines_root.is_dir():
        for routine_dir in sorted(path for path in routines_root.iterdir() if path.is_dir()):
            if _is_link_or_reparse(routine_dir):
                raise RuntimeError(f"Routine Definition 심볼릭 링크는 처리할 수 없습니다: {routine_dir.name}")
            relative_root = routine_dir.relative_to(root)
            for filename in ROUTINE_DERIVED_FILES:
                targets.append((str(relative_root / filename), routine_dir / filename))
            for dirname in ROUTINE_DERIVED_DIRS:
                targets.append((str(relative_root / dirname), routine_dir / dirname))
    return targets


def _stage_targets(
    root: Path,
    payload_root: Path,
    staged: list[tuple[Path, Path]],
) -> None:
    for relative, source in _reset_targets(root):
        if not source.exists():
            continue
        if _is_link_or_reparse(source):
            raise RuntimeError(f"심볼릭 링크는 초기화할 수 없습니다: {relative}")
        destination = payload_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        staged.append((source, destination))


def _preserved_runtime_hashes(staged: list[tuple[Path, Path]], root: Path) -> dict[str, str]:
    runtime_backup = next((backup for original, backup in staged if original == root / "runtime"), None)
    if runtime_backup is None:
        return {}
    return {
        filename: _sha256(runtime_backup / filename)
        for filename in PRESERVED_RUNTIME_FILES
        if (runtime_backup / filename).is_file()
    }


def _initialize_empty_state(root: Path, staged: list[tuple[Path, Path]]) -> None:
    for relative in RESET_DIRECTORIES:
        _project_child(root, relative).mkdir(parents=True, exist_ok=True)

    runtime_backup = next((backup for original, backup in staged if original == root / "runtime"), None)
    runtime_root = root / "runtime"
    if runtime_backup is not None:
        for filename in PRESERVED_RUNTIME_FILES:
            source = runtime_backup / filename
            if source.is_file():
                shutil.copy2(source, runtime_root / filename)

    from gui_operation_environment import default_operation_policy, write_operation_policy
    from state_policy import write_global_schedule

    write_operation_policy(
        default_operation_policy(),
        path=root / "operation_policy.json",
        preserve_buffer_response=False,
    )
    write_global_schedule("09:00:00", "13:30:00", path=root / "global_schedule.json")
    runtime_result = initialize_pristine_startup_runtime(runtime_root)
    if runtime_result.get("status") != STATUS_INITIALIZED:
        raise RuntimeError(
            "빈 Runtime 기본 구조 생성 실패: "
            + ", ".join(str(item) for item in runtime_result.get("issues", []))
        )


def _verify_empty_state(root: Path, preserved_hashes: dict[str, str]) -> None:
    for relative in RESET_DIRECTORIES:
        path = root / relative
        if not path.is_dir():
            raise RuntimeError(f"초기화 대상 디렉터리 생성 확인 실패: {relative}")
        if relative == "runtime":
            continue
        if any(path.iterdir()):
            raise RuntimeError(f"초기화 대상이 비어 있지 않습니다: {relative}")

    runtime_root = root / "runtime"
    canonical_runtime = set(startup_runtime_paths(runtime_root))
    allowed_runtime = canonical_runtime | set(preserved_hashes)
    actual_runtime = {path.name for path in runtime_root.iterdir() if path.is_file()}
    if actual_runtime != allowed_runtime:
        raise RuntimeError("Runtime 초기 상태 read-back 검증 실패")
    for filename, path in startup_runtime_paths(runtime_root).items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or any(
            isinstance(value, list) and value for value in payload.values()
        ):
            raise RuntimeError(f"{filename} 빈 Runtime 검증 실패")
    for filename, expected_hash in preserved_hashes.items():
        if _sha256(runtime_root / filename) != expected_hash:
            raise RuntimeError(f"{filename} 보존 검증 실패")
    if _directory_record_count(root / "groups", "group.json"):
        raise RuntimeError("Logical Group 제거 확인 실패")
    if _directory_record_count(root / "routine_instances", "instance.json"):
        raise RuntimeError("RoutineInstance 제거 확인 실패")
    if _directory_record_count(root / "stocks", "config.json"):
        raise RuntimeError("등록 Stock 제거 확인 실패")


def _rollback(
    root: Path,
    staged: list[tuple[Path, Path]],
    *,
    initialization_started: bool,
) -> bool:
    try:
        if initialization_started:
            for relative in RESET_DIRECTORIES + DELETE_FILES + RESET_FILES:
                _safe_remove(_project_child(root, relative), allowed_root=root)
        for original, backup in reversed(staged):
            if original.exists():
                if initialization_started:
                    _safe_remove(original, allowed_root=root)
                else:
                    raise RuntimeError(f"rollback target unexpectedly exists: {original}")
            original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, original)
        return all(original.exists() for original, _backup in staged)
    except Exception:
        return False


def execute_program_factory_reset(
    project_root: str | Path = PROJECT_ROOT,
    *,
    broker_connected: bool = False,
    quiesce: QuiesceCallback | None = None,
    resume_after_failure: ResumeCallback | None = None,
    now_factory: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Reset local user data without invoking or receiving a Broker order callable."""
    root = Path(project_root).resolve(strict=True)
    safety = validate_factory_reset_safety(root, broker_connected=broker_connected)
    if not safety["success"]:
        return safety
    preview = {key: safety[key] for key in (
        "removed_groups",
        "removed_instances",
        "removed_stocks",
        "removed_assignment_episodes",
        "removed_performance_events",
        "removed_runtime_items",
    )}
    quiesce_token: object = None
    if quiesce is not None:
        try:
            quiesce_token = quiesce()
        except Exception as exc:
            return _result(False, preview=preview, issues=[f"내부 작업 정지 실패: {exc}"])

    transaction_parent = _project_child(root, TRANSACTION_ROOT_NAME)
    transaction_root = transaction_parent / uuid4().hex
    payload_root = transaction_root / "payload"
    staged: list[tuple[Path, Path]] = []
    initialization_started = False
    rollback_complete: bool | None = None
    try:
        payload_root.mkdir(parents=True, exist_ok=False)
        marker = {
            "schema_version": "1.0",
            "operation": "COMPLETE_PROGRAM_INITIALIZATION",
            "state": "PENDING",
            "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        marker_path = transaction_root / "transaction.json"
        marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if json.loads(marker_path.read_text(encoding="utf-8")) != marker:
            raise RuntimeError("완전초기화 transaction marker 검증 실패")

        _stage_targets(root, payload_root, staged)
        preserved_hashes = _preserved_runtime_hashes(staged, root)
        initialization_started = True
        _initialize_empty_state(root, staged)
        _verify_empty_state(root, preserved_hashes)

        _safe_remove(transaction_root, allowed_root=transaction_parent)
        if transaction_parent.exists() and not any(transaction_parent.iterdir()):
            transaction_parent.rmdir()
        initialized_at = (now_factory or (lambda: datetime.now(timezone.utc)))().isoformat(timespec="seconds")
        return _result(True, preview=preview, initialized_at=initialized_at)
    except Exception as exc:
        rollback_complete = _rollback(
            root,
            staged,
            initialization_started=initialization_started,
        )
        if rollback_complete:
            try:
                _safe_remove(transaction_root, allowed_root=transaction_parent)
                if transaction_parent.exists() and not any(transaction_parent.iterdir()):
                    transaction_parent.rmdir()
            except Exception:
                rollback_complete = False
        if rollback_complete and resume_after_failure is not None:
            try:
                resume_after_failure(quiesce_token)
            except Exception:
                rollback_complete = False
        return _result(
            False,
            preview=preview,
            issues=[f"프로그램 초기화 실패: {exc}"],
            rollback_complete=rollback_complete,
        )
