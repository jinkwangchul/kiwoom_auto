"""Transactional deletion boundary for a discovered root Group."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

from group_recovery_repository import (
    clear_group_deletion_pending,
    list_group_recovery_controls,
    mark_group_deletion_pending,
    remove_pending_group_recovery,
)
from gui_routine_registry import scan_group_records
from routine_instance_registry import load_persisted_routine_instances
from runtime_io import read_json_dict
from stock_repository import StockRecord, StockRepository


@dataclass(frozen=True)
class GroupDeletionScope:
    group_id: str
    group_name: str
    group_path: Path
    instance_ids: tuple[str, ...]
    instance_dirs: tuple[Path, ...]
    stocks: tuple[StockRecord, ...]


@dataclass(frozen=True)
class GroupCompleteDeletionResult:
    success: bool
    error: str = ""
    blocked_reasons: tuple[str, ...] = ()
    deleted_instance_ids: tuple[str, ...] = ()
    cleared_stock_codes: tuple[str, ...] = ()


def collect_group_deletion_scope(
    project_root: Path | str,
    group_id: str,
) -> GroupDeletionScope:
    root = Path(project_root).resolve(strict=False)
    target = Path(str(group_id or "")).resolve(strict=False)
    if target.parent != root or not target.name.startswith("_"):
        raise ValueError("삭제할 Group 경로가 프로젝트 root 직하위가 아닙니다.")

    matching_groups = [
        group
        for group in scan_group_records(project_root=root, sync_recovery=False)
        if Path(group.path).resolve(strict=False) == target
    ]
    if len(matching_groups) > 1:
        raise ValueError("삭제할 Group을 하나로 확인할 수 없습니다.")
    if matching_groups:
        group_name = str(matching_groups[0].name or "").strip()
    else:
        matching_recoveries = [
            recovery
            for recovery in list_group_recovery_controls(root)
            if recovery.group_id == str(target)
        ]
        if len(matching_recoveries) != 1:
            raise ValueError("삭제할 Group을 하나로 확인할 수 없습니다.")
        group_name = matching_recoveries[0].display_name

    instances = [
        instance
        for instance in load_persisted_routine_instances(project_root=root)
        if str(getattr(instance, "group_id", "") or "").strip() == str(target)
    ]
    instance_ids = tuple(sorted(str(instance.instance_id) for instance in instances))
    instance_id_set = set(instance_ids)
    expected_instances_root = (root / "routine_instances").resolve(strict=False)
    instance_dirs: list[Path] = []
    for instance_id in instance_ids:
        instance_dir = (expected_instances_root / instance_id).resolve(strict=False)
        if instance_dir.parent != expected_instances_root or not instance_dir.is_dir():
            raise ValueError(f"삭제할 RoutineInstance 저장소를 확인할 수 없습니다: {instance_id}")
        instance_dirs.append(instance_dir)

    repository = StockRepository(root)
    stocks = tuple(
        stock
        for stock in repository.list_stocks()
        if (
            str(stock.routine or "").strip() == group_name
            or str(stock.assigned_routine_instance_id or "").strip() in instance_id_set
        )
    )
    return GroupDeletionScope(
        group_id=str(target),
        group_name=group_name,
        group_path=target,
        instance_ids=instance_ids,
        instance_dirs=tuple(instance_dirs),
        stocks=stocks,
    )


def validate_group_deletion_safety(
    scope: GroupDeletionScope,
    *,
    can_unassign: Callable[[str, str], tuple[bool, str, list[str]]],
    running_stock_dirs: Iterable[Path | str] = (),
) -> tuple[str, ...]:
    running = {
        str(Path(path).resolve(strict=False))
        for path in running_stock_dirs
    }
    reasons: list[str] = []
    for stock in scope.stocks:
        stock_dir = Path(stock.stock_path)
        if not stock_dir.is_absolute():
            stock_dir = scope.group_path.parent / stock_dir
        if str(stock_dir.resolve(strict=False)) in running:
            reasons.append(f"{stock.name}: 운영 중")
            continue
        allowed, _routine_name, stock_reasons = can_unassign(stock.code, stock.name)
        if not allowed:
            reason_text = ", ".join(str(reason) for reason in stock_reasons if str(reason))
            reasons.append(f"{stock.name}: {reason_text or '관계 해제 불가'}")
    return tuple(reasons)


def _restore_file(path: Path, payload: bytes) -> None:
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.rollback.tmp"
    temp_path.write_bytes(payload)
    os.replace(temp_path, path)


def _rollback_group_deletion(
    transaction_root: Path,
    moved_paths: list[tuple[Path, Path]],
    stock_backups: list[tuple[Path, bytes]],
    expected_original_paths: tuple[Path, ...],
) -> bool:
    for config_path, payload in reversed(stock_backups):
        try:
            _restore_file(config_path, payload)
        except Exception:
            pass
    for staged_path, original_path in reversed(moved_paths):
        try:
            if staged_path.exists() and not original_path.exists():
                original_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_path, original_path)
        except Exception:
            pass
    if transaction_root.exists():
        shutil.rmtree(transaction_root, ignore_errors=True)

    try:
        return (
            not transaction_root.exists()
            and all(path.is_dir() for path in expected_original_paths)
            and all(
                config_path.is_file() and config_path.read_bytes() == payload
                for config_path, payload in stock_backups
            )
        )
    except Exception:
        return False


def delete_group_completely(
    project_root: Path | str,
    scope: GroupDeletionScope,
    *,
    can_unassign: Callable[[str, str], tuple[bool, str, list[str]]],
    running_stock_dirs: Iterable[Path | str] = (),
) -> GroupCompleteDeletionResult:
    blocked = validate_group_deletion_safety(
        scope,
        can_unassign=can_unassign,
        running_stock_dirs=running_stock_dirs,
    )
    if blocked:
        return GroupCompleteDeletionResult(False, blocked_reasons=blocked)

    root = Path(project_root).resolve(strict=False)
    marker = mark_group_deletion_pending(root, scope.group_id)
    if not marker.success:
        return GroupCompleteDeletionResult(
            False,
            error=marker.error or "Group 삭제 의도를 저장하지 못했습니다.",
        )

    transaction_root = (
        root
        / "group_recovery"
        / f".{scope.group_path.name}.{uuid4().hex}.delete.tmp"
    )
    moved_paths: list[tuple[Path, Path]] = []
    stock_backups: list[tuple[Path, bytes]] = []
    cleared_codes: list[str] = []
    recovery_removed = False
    try:
        transaction_root.mkdir(parents=True)
        if scope.group_path.exists():
            staged_group = transaction_root / "group"
            os.replace(scope.group_path, staged_group)
            moved_paths.append((staged_group, scope.group_path))

        staged_instances = transaction_root / "instances"
        staged_instances.mkdir()
        for instance_dir in scope.instance_dirs:
            staged_instance = staged_instances / instance_dir.name
            os.replace(instance_dir, staged_instance)
            moved_paths.append((staged_instance, instance_dir))

        repository = StockRepository(root)
        for stock in scope.stocks:
            config_path = root / stock.stock_path / "config.json"
            if not config_path.is_file():
                raise RuntimeError(f"종목 config.json을 확인할 수 없습니다: {stock.code}")
            stock_backups.append((config_path, config_path.read_bytes()))
            if not repository.update_stock_routine(stock.code, stock.name, []):
                raise RuntimeError(f"종목 관계를 해제하지 못했습니다: {stock.code} {stock.name}")
            saved = read_json_dict(config_path)
            if (
                str(saved.get("assigned_routine_instance_id", "") or "").strip()
                or saved.get("routines")
            ):
                raise RuntimeError(f"종목 관계 해제 검증에 실패했습니다: {stock.code}")
            cleared_codes.append(stock.code)

        if scope.group_path.exists() or any(path.exists() for path in scope.instance_dirs):
            raise RuntimeError("Group 또는 RoutineInstance 삭제 staging 검증에 실패했습니다.")

        shutil.rmtree(transaction_root)
        if transaction_root.exists():
            raise RuntimeError("Group 삭제 transaction 정리에 실패했습니다.")

        recovery_result = remove_pending_group_recovery(root, scope.group_id)
        if not recovery_result.success:
            raise RuntimeError(
                recovery_result.error or "Group Recovery를 제거하지 못했습니다."
            )
        recovery_removed = True
        return GroupCompleteDeletionResult(
            True,
            deleted_instance_ids=scope.instance_ids,
            cleared_stock_codes=tuple(cleared_codes),
        )
    except Exception as exc:
        if not recovery_removed:
            rollback_complete = _rollback_group_deletion(
                transaction_root,
                moved_paths,
                stock_backups,
                tuple(original_path for _staged, original_path in moved_paths),
            )
            if rollback_complete:
                clear_result = clear_group_deletion_pending(root, scope.group_id)
                if not clear_result.success:
                    rollback_complete = False
            if not rollback_complete:
                return GroupCompleteDeletionResult(
                    False,
                    error=f"{exc}\n삭제 rollback을 완전하게 확인하지 못했습니다.",
                    deleted_instance_ids=scope.instance_ids,
                    cleared_stock_codes=tuple(cleared_codes),
                )
        return GroupCompleteDeletionResult(
            False,
            error=str(exc),
            deleted_instance_ids=scope.instance_ids,
            cleared_stock_codes=tuple(cleared_codes),
        )
