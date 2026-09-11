"""Transactional RoutineInstance deletion with canonical Stock unassignment."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from mock_validation_repository import MockValidationRepository
from routine_delete_policy import DeleteScopeBlock
from routine_instance_registry import routine_instance_by_id
from stock_repository import StockRecord, StockRepository


ROUTINE_INSTANCE_DELETE_ASSIGNED_STOCKS = (
    "ROUTINE_INSTANCE_DELETE_ASSIGNED_STOCKS"
)
ROUTINE_INSTANCE_DELETE_MOCK_REGISTRATIONS = (
    "ROUTINE_INSTANCE_DELETE_MOCK_REGISTRATIONS"
)
ROUTINE_INSTANCE_DELETE_ASSIGNED_STOCKS_MESSAGE = (
    "해당루틴은 삭제 불가합니다.\n"
    "등록된 종목을 모두 해제하세요."
)
ROUTINE_INSTANCE_DELETE_MOCK_REGISTRATIONS_MESSAGE = (
    "모의검증 등록상태가 남아 있어 루틴을 등록해제할 수 없습니다."
)


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
    reason_code: str = ""
    assigned_stock_count: int = 0
    mock_registration_count: int = 0


@dataclass(frozen=True)
class RoutineInstanceMockRegistration:
    validation_session_id: str
    stock_code: str
    routine_instance_ids: tuple[str, ...]
    document: dict[str, object]


def assigned_stocks_for_routine_instance(
    project_root: Path | str,
    instance_id: str,
) -> tuple[StockRecord, ...]:
    clean_id = str(instance_id or "").strip()
    return tuple(
        stock
        for stock in StockRepository(Path(project_root)).list_stocks()
        if stock.assigned_routine_instance_id == clean_id
    )


def current_mock_registrations_for_routine_instance(
    project_root: Path | str,
    instance_id: str,
) -> tuple[RoutineInstanceMockRegistration, ...]:
    root = Path(project_root).resolve(strict=False)
    clean_id = str(instance_id or "").strip()
    repository = MockValidationRepository(
        root / "mock_validation",
        project_root=root,
    )
    matches: list[RoutineInstanceMockRegistration] = []
    for document in repository.current_sessions():
        references = document.get("reference_snapshot", {}).get("routine_instances", ())
        instance_ids = tuple(
            str(item.get("routine_instance_id", "") or "").strip()
            for item in references
            if isinstance(item, dict)
            and str(item.get("routine_instance_id", "") or "").strip()
        )
        if clean_id not in instance_ids:
            continue
        session = document.get("session", {})
        matches.append(
            RoutineInstanceMockRegistration(
                validation_session_id=str(
                    session.get("validation_session_id", "") or ""
                ).strip(),
                stock_code=str(session.get("stock_code", "") or "").strip(),
                routine_instance_ids=instance_ids,
                document=document,
            )
        )
    return tuple(sorted(matches, key=lambda item: (item.stock_code, item.validation_session_id)))


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
    stocks = assigned_stocks_for_routine_instance(root, clean_id)
    return RoutineInstanceDeletionScope(
        root,
        clean_id,
        str(instance.display_name or clean_id),
        str(getattr(instance, "group_id", "") or "").strip(),
        str(instance.definition_id or "").strip(),
        instance_dir,
        stocks,
    )


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
    del running_stock_dirs
    try:
        assigned_stocks = assigned_stocks_for_routine_instance(
            scope.project_root,
            scope.instance_id,
        )
    except Exception as exc:
        return RoutineInstanceDeletionResult(
            False,
            error=str(exc) or type(exc).__name__,
            reason_code="ROUTINE_INSTANCE_DELETE_DEPENDENCY_UNAVAILABLE",
        )
    if assigned_stocks:
        return RoutineInstanceDeletionResult(
            False,
            error=ROUTINE_INSTANCE_DELETE_ASSIGNED_STOCKS_MESSAGE,
            blocked=(
                DeleteScopeBlock(
                    "",
                    "",
                    ROUTINE_INSTANCE_DELETE_ASSIGNED_STOCKS,
                    ROUTINE_INSTANCE_DELETE_ASSIGNED_STOCKS_MESSAGE,
                ),
            ),
            reason_code=ROUTINE_INSTANCE_DELETE_ASSIGNED_STOCKS,
            assigned_stock_count=len(assigned_stocks),
        )
    try:
        mock_registrations = current_mock_registrations_for_routine_instance(
            scope.project_root,
            scope.instance_id,
        )
    except Exception as exc:
        return RoutineInstanceDeletionResult(
            False,
            error=str(exc) or type(exc).__name__,
            reason_code="ROUTINE_INSTANCE_DELETE_DEPENDENCY_UNAVAILABLE",
        )
    if mock_registrations:
        return RoutineInstanceDeletionResult(
            False,
            error=ROUTINE_INSTANCE_DELETE_MOCK_REGISTRATIONS_MESSAGE,
            blocked=(
                DeleteScopeBlock(
                    "",
                    "",
                    ROUTINE_INSTANCE_DELETE_MOCK_REGISTRATIONS,
                    ROUTINE_INSTANCE_DELETE_MOCK_REGISTRATIONS_MESSAGE,
                ),
            ),
            reason_code=ROUTINE_INSTANCE_DELETE_MOCK_REGISTRATIONS,
            mock_registration_count=len(mock_registrations),
        )

    instance_snapshot = {
        path.relative_to(scope.instance_dir): path.read_bytes()
        for path in scope.instance_dir.rglob("*")
        if path.is_file()
    }
    staged_instance = scope.instance_dir.with_name(
        f".{scope.instance_id}.{uuid4().hex}.delete.tmp"
    )
    try:
        os.replace(scope.instance_dir, staged_instance)
        if scope.instance_dir.exists():
            raise RuntimeError("RoutineInstance 삭제 staging 검증에 실패했습니다.")
        shutil.rmtree(staged_instance)
        if scope.instance_dir.exists() or staged_instance.exists():
            raise RuntimeError("RoutineInstance 저장소 제거 검증에 실패했습니다.")
        return RoutineInstanceDeletionResult(True)
    except Exception as exc:
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
        rollback_complete = instance_restored
        return RoutineInstanceDeletionResult(
            False,
            error=(str(exc) if rollback_complete else f"{exc}\n삭제 rollback을 완전하게 확인하지 못했습니다."),
        )
