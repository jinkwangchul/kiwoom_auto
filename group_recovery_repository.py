"""Crash-safe snapshots for root Group folders.

This repository backs up Group folder contents only. RoutineInstance ownership
continues to be defined by ``routine_instances/<UUID>/instance.json.group_id``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


GROUP_RECOVERY_SCHEMA_VERSION = "1.0"
GROUP_RECOVERY_DIR_NAME = "group_recovery"
_GROUP_RECOVERY_LOCK = threading.RLock()


@dataclass(frozen=True)
class GroupRecoverySyncResult:
    success: bool
    changed: bool = False
    recovery_path: Path | None = None
    error: str = ""


@dataclass(frozen=True)
class GroupRecoveryRestoreResult:
    success: bool
    restored: bool = False
    display_name: str = ""
    target_path: Path | None = None
    error: str = ""


@dataclass(frozen=True)
class GroupRecoveryControlRecord:
    group_id: str
    display_name: str
    target_path: Path
    recovery_path: Path
    deletion_pending: bool = False


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"Group snapshot does not support symlinks: {relative}")
        if path.is_dir():
            digest.update(b"D\0")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            continue
        if not path.is_file():
            raise ValueError(f"Unsupported Group entry: {relative}")
        digest.update(b"F\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _promote_staged_recovery(staged: Path, final: Path) -> None:
    if not final.exists():
        os.replace(staged, final)
        return

    previous = final.parent / f".{final.name}.{uuid4().hex}.previous"
    os.replace(final, previous)
    try:
        os.replace(staged, final)
    except Exception:
        if not final.exists() and previous.exists():
            os.replace(previous, final)
        raise
    shutil.rmtree(previous)


def sync_group_recovery_snapshot(
    project_root: Path | str,
    group: object,
) -> GroupRecoverySyncResult:
    with _GROUP_RECOVERY_LOCK:
        return _sync_group_recovery_snapshot(project_root, group)


def _sync_group_recovery_snapshot(
    project_root: Path | str,
    group: object,
) -> GroupRecoverySyncResult:
    root = Path(project_root).resolve(strict=False)
    source = Path(getattr(group, "path", "")).resolve(strict=False)
    display_name = str(getattr(group, "name", "") or "").strip()
    recovery_root = root / GROUP_RECOVERY_DIR_NAME
    final = recovery_root / source.name

    try:
        if source.parent != root or not source.name.startswith("_"):
            raise ValueError("Group source must be a root folder whose name starts with '_'")
        if not source.is_dir() or not (source / "budget.json").is_file():
            raise ValueError("Group source is not a valid discovered Group folder")

        source_digest = _tree_digest(source)
        manifest_path = final / "manifest.json"
        snapshot_path = final / "snapshot"
        existing_manifest = _read_manifest(manifest_path)
        if existing_manifest.get("deletion_pending") is True:
            return GroupRecoverySyncResult(True, False, final)
        if (
            snapshot_path.is_dir()
            and str(existing_manifest.get("content_digest", "")) == source_digest
            and _tree_digest(snapshot_path) == source_digest
        ):
            return GroupRecoverySyncResult(True, False, final)

        recovery_root.mkdir(parents=True, exist_ok=True)
        staged = recovery_root / f".{source.name}.{uuid4().hex}.tmp"
        staged.mkdir()
        try:
            staged_snapshot = staged / "snapshot"
            shutil.copytree(source, staged_snapshot, copy_function=shutil.copy2)
            snapshot_digest = _tree_digest(staged_snapshot)
            if snapshot_digest != source_digest or _tree_digest(source) != source_digest:
                raise RuntimeError("Group changed while its recovery snapshot was being created")

            manifest = {
                "schema_version": GROUP_RECOVERY_SCHEMA_VERSION,
                "group_id": str(source),
                "group_folder_name": source.name,
                "display_name": display_name,
                "source_path": str(source),
                "snapshot_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "content_digest": source_digest,
            }
            _write_manifest(staged / "manifest.json", manifest)
            if _tree_digest(staged_snapshot) != source_digest:
                raise RuntimeError("Staged Group recovery verification failed")
            _promote_staged_recovery(staged, final)
        finally:
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)
        return GroupRecoverySyncResult(True, True, final)
    except Exception as exc:
        return GroupRecoverySyncResult(False, False, final, str(exc))


def _validated_restore_contract(
    project_root: Path,
    recovery_path: Path,
) -> tuple[dict[str, Any], Path, Path, str]:
    manifest = _read_manifest(recovery_path / "manifest.json")
    if str(manifest.get("schema_version", "")) != GROUP_RECOVERY_SCHEMA_VERSION:
        raise ValueError("Group recovery schema_version is invalid")

    folder_name = str(manifest.get("group_folder_name", "") or "").strip()
    display_name = str(manifest.get("display_name", "") or "").strip()
    if not folder_name.startswith("_") or folder_name != recovery_path.name:
        raise ValueError("Group recovery folder identity is invalid")
    if not display_name:
        raise ValueError("Group recovery display_name is missing")

    target = (project_root / folder_name).resolve(strict=False)
    expected_path = str(target)
    if target.parent != project_root:
        raise ValueError("Group recovery target is outside the project root")
    if str(manifest.get("group_id", "") or "") != expected_path:
        raise ValueError("Group recovery group_id does not match its root target")
    if str(manifest.get("source_path", "") or "") != expected_path:
        raise ValueError("Group recovery source_path does not match its root target")

    snapshot = recovery_path / "snapshot"
    if not snapshot.is_dir() or not (snapshot / "budget.json").is_file():
        raise ValueError("Group recovery snapshot is incomplete")
    content_digest = str(manifest.get("content_digest", "") or "").strip()
    if not content_digest or _tree_digest(snapshot) != content_digest:
        raise ValueError("Group recovery snapshot digest is invalid")
    return manifest, snapshot, target, content_digest


def restore_missing_group_snapshots(
    project_root: Path | str,
) -> tuple[GroupRecoveryRestoreResult, ...]:
    """Restore absent root Groups from validated recovery snapshots."""
    with _GROUP_RECOVERY_LOCK:
        return _restore_missing_group_snapshots(project_root)


def _restore_validated_recovery(
    project_root: Path,
    recovery_path: Path,
    *,
    pending_is_error: bool = False,
) -> GroupRecoveryRestoreResult:
    display_name = ""
    target: Path | None = None
    staged: Path | None = None
    promoted = False
    try:
        manifest, snapshot, target, content_digest = _validated_restore_contract(
            project_root,
            recovery_path,
        )
        display_name = str(manifest["display_name"])
        if manifest.get("deletion_pending") is True:
            return GroupRecoveryRestoreResult(
                not pending_is_error,
                False,
                display_name,
                target,
                "Group deletion is pending",
            )
        if target.exists():
            return GroupRecoveryRestoreResult(True, False, display_name, target)

        staged = project_root / f".{recovery_path.name}.{uuid4().hex}.restore.tmp"
        shutil.copytree(snapshot, staged, copy_function=shutil.copy2)
        if _tree_digest(staged) != content_digest:
            raise RuntimeError("Restored Group staging verification failed")
        if target.exists():
            shutil.rmtree(staged)
            staged = None
            return GroupRecoveryRestoreResult(True, False, display_name, target)
        os.replace(staged, target)
        staged = None
        promoted = True
        if not target.is_dir() or _tree_digest(target) != content_digest:
            raise RuntimeError("Restored Group target verification failed")
        return GroupRecoveryRestoreResult(True, True, display_name, target)
    except Exception as exc:
        if staged is not None and staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        if promoted and target is not None and target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return GroupRecoveryRestoreResult(
            False,
            False,
            display_name,
            target,
            str(exc),
        )


def _restore_missing_group_snapshots(
    project_root: Path | str,
) -> tuple[GroupRecoveryRestoreResult, ...]:
    root = Path(project_root).resolve(strict=False)
    recovery_root = root / GROUP_RECOVERY_DIR_NAME
    if not recovery_root.is_dir():
        return ()

    results: list[GroupRecoveryRestoreResult] = []
    recovery_paths = sorted(
        (
            path
            for path in recovery_root.iterdir()
            if path.is_dir() and path.name.startswith("_")
        ),
        key=lambda path: path.name.casefold(),
    )
    for recovery_path in recovery_paths:
        results.append(_restore_validated_recovery(root, recovery_path))
    return tuple(results)


def restore_group_snapshot(
    project_root: Path | str,
    group_id: str,
) -> GroupRecoveryRestoreResult:
    """Manually retry one validated Recovery without touching its marker."""
    with _GROUP_RECOVERY_LOCK:
        root = Path(project_root).resolve(strict=False)
        target = Path(str(group_id or "")).resolve(strict=False)
        if target.parent != root or not target.name.startswith("_"):
            return GroupRecoveryRestoreResult(
                False,
                target_path=target,
                error="Group recovery target is invalid",
            )
        return _restore_validated_recovery(
            root,
            root / GROUP_RECOVERY_DIR_NAME / target.name,
            pending_is_error=True,
        )


def list_group_recovery_controls(
    project_root: Path | str,
) -> tuple[GroupRecoveryControlRecord, ...]:
    """Return validated missing-Group recoveries for Main control rows."""
    with _GROUP_RECOVERY_LOCK:
        root = Path(project_root).resolve(strict=False)
        recovery_root = root / GROUP_RECOVERY_DIR_NAME
        if not recovery_root.is_dir():
            return ()
        controls: list[GroupRecoveryControlRecord] = []
        for recovery_path in sorted(recovery_root.iterdir(), key=lambda item: item.name.casefold()):
            if not recovery_path.is_dir() or not recovery_path.name.startswith("_"):
                continue
            try:
                manifest, _snapshot, target, _digest = _validated_restore_contract(
                    root,
                    recovery_path,
                )
            except Exception:
                continue
            if target.exists():
                continue
            controls.append(
                GroupRecoveryControlRecord(
                    group_id=str(target),
                    display_name=str(manifest["display_name"]),
                    target_path=target,
                    recovery_path=recovery_path,
                    deletion_pending=manifest.get("deletion_pending") is True,
                )
            )
        return tuple(controls)


def mark_group_deletion_pending(
    project_root: Path | str,
    group_id: str,
) -> GroupRecoverySyncResult:
    with _GROUP_RECOVERY_LOCK:
        root = Path(project_root).resolve(strict=False)
        target = Path(str(group_id or "")).resolve(strict=False)
        recovery_path = root / GROUP_RECOVERY_DIR_NAME / target.name
        temp_path = recovery_path / f".manifest.{uuid4().hex}.tmp"
        try:
            manifest, _snapshot, validated_target, _digest = _validated_restore_contract(
                root,
                recovery_path,
            )
            if validated_target != target:
                raise ValueError("Group deletion target does not match its Recovery")
            if manifest.get("deletion_pending") is True:
                return GroupRecoverySyncResult(True, False, recovery_path)
            marked = dict(manifest)
            marked["deletion_pending"] = True
            marked["deletion_requested_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            _write_manifest(temp_path, marked)
            verified = _read_manifest(temp_path)
            if verified != marked or verified.get("deletion_pending") is not True:
                raise RuntimeError("Group deletion marker verification failed")
            os.replace(temp_path, recovery_path / "manifest.json")
            if _read_manifest(recovery_path / "manifest.json") != marked:
                raise RuntimeError("Durable Group deletion marker verification failed")
            return GroupRecoverySyncResult(True, True, recovery_path)
        except Exception as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            return GroupRecoverySyncResult(False, False, recovery_path, str(exc))


def clear_group_deletion_pending(
    project_root: Path | str,
    group_id: str,
) -> GroupRecoverySyncResult:
    with _GROUP_RECOVERY_LOCK:
        root = Path(project_root).resolve(strict=False)
        target = Path(str(group_id or "")).resolve(strict=False)
        recovery_path = root / GROUP_RECOVERY_DIR_NAME / target.name
        temp_path = recovery_path / f".manifest.{uuid4().hex}.tmp"
        try:
            manifest, _snapshot, validated_target, _digest = _validated_restore_contract(
                root,
                recovery_path,
            )
            if validated_target != target:
                raise ValueError("Group rollback target does not match its Recovery")
            if manifest.get("deletion_pending") is not True:
                raise ValueError("Group Recovery has no durable deletion marker")
            restored = dict(manifest)
            restored.pop("deletion_pending", None)
            restored.pop("deletion_requested_at", None)
            _write_manifest(temp_path, restored)
            if _read_manifest(temp_path) != restored:
                raise RuntimeError("Group deletion marker clear verification failed")
            os.replace(temp_path, recovery_path / "manifest.json")
            if _read_manifest(recovery_path / "manifest.json") != restored:
                raise RuntimeError("Durable Group deletion marker clear verification failed")
            return GroupRecoverySyncResult(True, True, recovery_path)
        except Exception as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            return GroupRecoverySyncResult(False, False, recovery_path, str(exc))


def remove_pending_group_recovery(
    project_root: Path | str,
    group_id: str,
) -> GroupRecoverySyncResult:
    with _GROUP_RECOVERY_LOCK:
        root = Path(project_root).resolve(strict=False)
        target = Path(str(group_id or "")).resolve(strict=False)
        recovery_path = root / GROUP_RECOVERY_DIR_NAME / target.name
        try:
            manifest, _snapshot, validated_target, _digest = _validated_restore_contract(
                root,
                recovery_path,
            )
            if validated_target != target:
                raise ValueError("Group deletion target does not match its Recovery")
            if manifest.get("deletion_pending") is not True:
                raise ValueError("Group Recovery has no durable deletion marker")
            shutil.rmtree(recovery_path)
            if recovery_path.exists():
                raise RuntimeError("Group Recovery removal verification failed")
            return GroupRecoverySyncResult(True, True, recovery_path)
        except Exception as exc:
            return GroupRecoverySyncResult(False, False, recovery_path, str(exc))
