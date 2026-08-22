"""Durable Group deletion intent markers independent from Group Recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4


DELETE_OPERATION = "DELETE"
DELETE_PENDING_STATE = "PENDING"
_LOCK = RLock()


@dataclass(frozen=True)
class GroupDeleteMarkerResult:
    success: bool
    changed: bool = False
    marker_path: Path | None = None
    error: str = ""


def _clean_group_id(group_id: object) -> str:
    try:
        return str(UUID(str(group_id or "").strip()))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("group_id must be a UUID")


def group_delete_marker_path(project_root: Path | str, group_id: object) -> Path:
    clean_group_id = _clean_group_id(group_id)
    return (
        Path(project_root).resolve(strict=False)
        / "groups"
        / ".transactions"
        / f"{clean_group_id}.delete.json"
    )


def _read_marker(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _valid_marker(payload: dict[str, object], group_id: str) -> bool:
    return (
        str(payload.get("group_id", "") or "") == group_id
        and payload.get("operation") == DELETE_OPERATION
        and payload.get("state") == DELETE_PENDING_STATE
        and bool(str(payload.get("requested_at", "") or "").strip())
    )


def mark_group_delete_pending(
    project_root: Path | str,
    group_id: object,
) -> GroupDeleteMarkerResult:
    with _LOCK:
        try:
            clean_group_id = _clean_group_id(group_id)
        except ValueError as exc:
            return GroupDeleteMarkerResult(False, error=str(exc))
        marker_path = group_delete_marker_path(project_root, clean_group_id)
        temp_path = marker_path.parent / f".{marker_path.name}.{uuid4().hex}.tmp"
        try:
            existing = _read_marker(marker_path)
            if _valid_marker(existing, clean_group_id):
                return GroupDeleteMarkerResult(True, False, marker_path)
            if marker_path.exists():
                raise ValueError("existing Group deletion marker is invalid")
            payload = {
                "group_id": clean_group_id,
                "operation": DELETE_OPERATION,
                "state": DELETE_PENDING_STATE,
                "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if not _valid_marker(_read_marker(temp_path), clean_group_id):
                raise RuntimeError("staged Group deletion marker verification failed")
            os.replace(temp_path, marker_path)
            if not _valid_marker(_read_marker(marker_path), clean_group_id):
                raise RuntimeError("durable Group deletion marker verification failed")
            return GroupDeleteMarkerResult(True, True, marker_path)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            return GroupDeleteMarkerResult(False, False, marker_path, str(exc))


def clear_group_delete_pending(
    project_root: Path | str,
    group_id: object,
) -> GroupDeleteMarkerResult:
    with _LOCK:
        try:
            clean_group_id = _clean_group_id(group_id)
        except ValueError as exc:
            return GroupDeleteMarkerResult(False, error=str(exc))
        marker_path = group_delete_marker_path(project_root, clean_group_id)
        try:
            if not marker_path.exists():
                return GroupDeleteMarkerResult(True, False, marker_path)
            if not _valid_marker(_read_marker(marker_path), clean_group_id):
                raise ValueError("Group deletion marker is invalid")
            marker_path.unlink()
            if marker_path.exists():
                raise RuntimeError("Group deletion marker removal verification failed")
            return GroupDeleteMarkerResult(True, True, marker_path)
        except Exception as exc:
            return GroupDeleteMarkerResult(False, False, marker_path, str(exc))


def group_delete_pending(project_root: Path | str, group_id: object) -> bool:
    try:
        clean_group_id = _clean_group_id(group_id)
        marker_path = group_delete_marker_path(project_root, clean_group_id)
        if not marker_path.exists():
            return False
        # A damaged marker is an uncertain deletion transaction and therefore
        # remains fail-closed until an operator resolves it.
        return True
    except Exception:
        return False
