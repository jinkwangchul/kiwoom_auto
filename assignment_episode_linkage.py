"""Atomic Production linkage between Stock assignment and canonical episodes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
from gui_routine_registry import scan_group_records
from routine_instance_repository import RoutineInstanceRepository


@dataclass(frozen=True)
class AssignmentLinkageResult:
    success: bool
    changed: bool = False
    bootstrapped: bool = False
    episode_id: str = ""
    error_code: str = ""
    error: str = ""
    rollback_complete: bool = True


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore(path: Path, payload: bytes | None) -> bool:
    try:
        if payload is None:
            path.unlink(missing_ok=True)
            parent = path.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
            return not path.exists()
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.rollback.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(payload)
        os.replace(temporary, path)
        return path.read_bytes() == payload
    except Exception:
        return False


def assignment_target_from_config(
    project_root: Path | str,
    config: dict[str, Any],
) -> AssignmentEpisodeTarget:
    instance_id = str(config.get("assigned_routine_instance_id") or "").strip()
    if not instance_id:
        return AssignmentEpisodeTarget.unassigned()
    root = Path(project_root)
    instance = RoutineInstanceRepository(root).get_instance(instance_id)
    if instance is None:
        raise ValueError(f"assigned RoutineInstance does not exist: {instance_id}")
    group_id = str(instance.group_id or "").strip()
    if not group_id:
        raise ValueError(f"assigned RoutineInstance has no group_id: {instance_id}")
    group = next(
        (item for item in scan_group_records(project_root=root) if item.group_id == group_id),
        None,
    )
    if group is None:
        raise ValueError(f"assigned Logical Group does not exist: {group_id}")
    return AssignmentEpisodeTarget.assigned(
        instance_id=instance.instance_id,
        group_id=group.group_id,
        definition_id=instance.definition_id,
        instance_name_snapshot=instance.display_name,
        group_name_snapshot=group.display_name,
    )


def ensure_current_assignment_episode(
    project_root: Path | str,
    stock_code: str,
    config: dict[str, Any],
    *,
    observed_at: str | datetime,
    source: str,
    repository: CanonicalAssignmentEpisodeRepository | None = None,
):
    """Return the exact open Episode, bootstrapping only at observation time."""
    root = Path(project_root)
    repo = repository or CanonicalAssignmentEpisodeRepository(root)
    target = assignment_target_from_config(root, config)
    current = repo.get_open_episode(stock_code)
    if current is None:
        reason = (
            "BOOTSTRAP_CURRENT_ASSIGNED"
            if target.ownership_kind == "ASSIGNED"
            else "BOOTSTRAP_UNASSIGNED"
        )
        result = repo.open_episode(
            stock_code,
            target,
            started_at=observed_at,
            start_reason=reason,
            source=source,
        )
        if not result.success or result.opened_episode is None:
            raise RuntimeError(result.error or "assignment Episode bootstrap failed")
        return result.opened_episode, True
    if current.target().identity_key() != target.identity_key():
        raise RuntimeError(
            "current Stock assignment and open canonical Episode do not match"
        )
    return current, False


def commit_assignment_with_episode(
    project_root: Path | str,
    stock_code: str,
    config_path: Path | str,
    before_config: dict[str, Any],
    after_config: dict[str, Any],
    *,
    changed_at: str | datetime,
    reason: str,
    source: str,
    before_target: AssignmentEpisodeTarget | None = None,
    after_target: AssignmentEpisodeTarget | None = None,
) -> AssignmentLinkageResult:
    """Commit relation and Episode as one rollback-verified mutation boundary."""
    root = Path(project_root)
    config_target = Path(config_path)
    repository = CanonicalAssignmentEpisodeRepository(root)
    episode_path = repository.document_path(stock_code)
    config_before = config_target.read_bytes() if config_target.exists() else None
    episode_before = episode_path.read_bytes() if episode_path.exists() else None
    bootstrapped = False
    try:
        before_target = before_target or assignment_target_from_config(root, before_config)
        after_target = after_target or assignment_target_from_config(root, after_config)
        open_episode = repository.get_open_episode(stock_code)
        if open_episode is None:
            bootstrap_reason = (
                "BOOTSTRAP_CURRENT_ASSIGNED"
                if before_target.ownership_kind == "ASSIGNED"
                else "BOOTSTRAP_UNASSIGNED"
            )
            opened = repository.open_episode(
                stock_code,
                before_target,
                started_at=changed_at,
                start_reason=bootstrap_reason,
                source=source,
            )
            if not opened.success:
                raise RuntimeError(opened.error or "assignment Episode bootstrap failed")
            open_episode = opened.opened_episode
            bootstrapped = True
        if open_episode is None or open_episode.target().identity_key() != before_target.identity_key():
            raise RuntimeError("current relation does not match the open assignment Episode")

        transition = repository.transition_episode(
            stock_code,
            after_target,
            changed_at=changed_at,
            start_reason=reason,
            end_reason=reason,
            source=source,
        )
        if not transition.success or transition.opened_episode is None:
            raise RuntimeError(transition.error or "assignment Episode transition failed")

        _atomic_write_json(config_target, after_config)
        persisted = json.loads(config_target.read_text(encoding="utf-8"))
        if persisted != after_config:
            raise RuntimeError("Stock assignment read-back does not match")
        verified = repository.get_open_episode(stock_code)
        if verified is None or verified.target().identity_key() != after_target.identity_key():
            raise RuntimeError("assignment Episode read-back does not match")
        return AssignmentLinkageResult(
            True,
            changed=before_target.identity_key() != after_target.identity_key(),
            bootstrapped=bootstrapped,
            episode_id=verified.episode_id,
        )
    except Exception as exc:
        config_restored = _restore(config_target, config_before)
        episode_restored = _restore(episode_path, episode_before)
        rollback_complete = config_restored and episode_restored
        return AssignmentLinkageResult(
            False,
            error_code=(
                "ASSIGNMENT_EPISODE_COMMIT_FAILED"
                if rollback_complete
                else "ASSIGNMENT_EPISODE_FAIL_CLOSED"
            ),
            error=str(exc),
            rollback_complete=rollback_complete,
        )
