"""Transactional RoutineInstance deletion with canonical Stock unassignment."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from assignment_episode_linkage import unassign_stock_routine
from assignment_episode_repository import CanonicalAssignmentEpisodeRepository
from routine_delete_policy import DeleteScopeBlock, preview_delete_scope
from routine_instance_registry import routine_instance_by_id
from runtime_io import read_json_dict
from stock_repository import StockRecord, StockRepository


@dataclass(frozen=True)
class RoutineInstanceDeletionScope:
    project_root: Path
    instance_id: str
    instance_name: str
    group_id: str
    definition_id: str
    instance_dir: Path
    stocks: tuple[StockRecord, ...]


@dataclass(frozen=True)
class RoutineInstanceDeletionResult:
    success: bool
    error: str = ""
    blocked: tuple[DeleteScopeBlock, ...] = ()
    cleared_stock_codes: tuple[str, ...] = ()


def collect_routine_instance_deletion_scope(
    project_root: Path | str,
    instance_id: str,
) -> RoutineInstanceDeletionScope:
    root = Path(project_root).resolve(strict=False)
    clean_id = str(instance_id or "").strip()
    instance = routine_instance_by_id(clean_id, project_root=root)
    if instance is None:
        raise ValueError("삭제할 등록 루틴을 찾을 수 없습니다.")
    instance_dir = (root / "routine_instances" / clean_id).resolve(strict=False)
    expected_root = (root / "routine_instances").resolve(strict=False)
    if instance_dir.parent != expected_root or not instance_dir.is_dir():
        raise ValueError("삭제할 RoutineInstance 저장소를 확인할 수 없습니다.")
    stocks = tuple(
        stock
        for stock in StockRepository(root).list_stocks()
        if stock.assigned_routine_instance_id == clean_id
    )
    return RoutineInstanceDeletionScope(
        root,
        clean_id,
        str(instance.display_name or clean_id),
        str(getattr(instance, "group_id", "") or "").strip(),
        str(instance.definition_id or "").strip(),
        instance_dir,
        stocks,
    )


def _restore(path: Path, payload: bytes | None) -> bool:
    try:
        if payload is None:
            path.unlink(missing_ok=True)
            return not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.rollback.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)
        return path.read_bytes() == payload
    except Exception:
        return False


def _restore_instance_directory(
    instance_dir: Path,
    staged_dir: Path,
    snapshot: dict[Path, bytes],
) -> bool:
    try:
        if instance_dir.exists():
            shutil.rmtree(instance_dir)
        if staged_dir.exists():
            shutil.rmtree(staged_dir)
        for relative_path, payload in snapshot.items():
            target = instance_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        return instance_dir.is_dir() and all(
            (instance_dir / relative_path).read_bytes() == payload
            for relative_path, payload in snapshot.items()
        )
    except Exception:
        return False


def delete_routine_instance_completely(
    scope: RoutineInstanceDeletionScope,
    *,
    running_stock_dirs: Iterable[Path | str] = (),
) -> RoutineInstanceDeletionResult:
    blocked = preview_delete_scope(
        scope.project_root,
        scope.stocks,
        running_stock_dirs=running_stock_dirs,
    )
    if blocked:
        return RoutineInstanceDeletionResult(False, blocked=blocked)

    stock_backups: list[tuple[Path, bytes | None]] = []
    cleared: list[str] = []
    repository = StockRepository(scope.project_root)
    episode_repository = CanonicalAssignmentEpisodeRepository(scope.project_root)
    instance_snapshot = {
        path.relative_to(scope.instance_dir): path.read_bytes()
        for path in scope.instance_dir.rglob("*")
        if path.is_file()
    }
    staged_instance = scope.instance_dir.with_name(
        f".{scope.instance_id}.{uuid4().hex}.delete.tmp"
    )
    try:
        for stock in scope.stocks:
            config_path = scope.project_root / stock.stock_path / "config.json"
            episode_path = episode_repository.document_path(stock.code)
            stock_backups.extend(
                (
                    (config_path, config_path.read_bytes()),
                    (episode_path, episode_path.read_bytes() if episode_path.exists() else None),
                )
            )
            result = unassign_stock_routine(
                repository.project_root,
                stock.code,
                stock.name,
                [],
                expected_instance_id=scope.instance_id,
                stock_repository=repository,
            )
            if not result.ok:
                raise RuntimeError(f"종목 관계를 해제하지 못했습니다: {stock.code} {stock.name}")
            saved = read_json_dict(config_path)
            if str(saved.get("assigned_routine_instance_id", "") or "").strip() or saved.get("routines"):
                raise RuntimeError(f"종목 관계 해제 검증에 실패했습니다: {stock.code}")
            cleared.append(stock.code)

        os.replace(scope.instance_dir, staged_instance)
        if scope.instance_dir.exists():
            raise RuntimeError("RoutineInstance 삭제 staging 검증에 실패했습니다.")
        shutil.rmtree(staged_instance)
        if scope.instance_dir.exists() or staged_instance.exists():
            raise RuntimeError("RoutineInstance 저장소 제거 검증에 실패했습니다.")
        return RoutineInstanceDeletionResult(True, cleared_stock_codes=tuple(cleared))
    except Exception as exc:
        restore_results = [
            _restore(path, payload) for path, payload in reversed(stock_backups)
        ]
        instance_restored = (
            scope.instance_dir.is_dir()
            and all(
                (scope.instance_dir / relative_path).is_file()
                and (scope.instance_dir / relative_path).read_bytes() == payload
                for relative_path, payload in instance_snapshot.items()
            )
        ) or _restore_instance_directory(
            scope.instance_dir,
            staged_instance,
            instance_snapshot,
        )
        rollback_complete = all(restore_results) and instance_restored
        return RoutineInstanceDeletionResult(
            False,
            error=(str(exc) if rollback_complete else f"{exc}\n삭제 rollback을 완전하게 확인하지 못했습니다."),
            cleared_stock_codes=tuple(cleared),
        )
