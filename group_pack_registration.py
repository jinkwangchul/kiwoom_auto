# -*- coding: utf-8 -*-
"""Validated Group Pack inspection and atomic registration boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from typing import Any
from uuid import uuid4
import zipfile

from logical_group_registry import LogicalGroupRecord, LogicalGroupRepository
from routine_instance_registry import routine_definition_by_id
from routine_package_contract import validate_routine_definition_capabilities


PROJECT_ROOT = Path(__file__).resolve().parent
GROUP_PACK_SCHEMA_VERSION = "1.0"
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_FORBIDDEN_DESTINATION_ROOTS = {
    ".git",
    "account",
    "accounts",
    "archived_stocks",
    "artifacts",
    "docs",
    "group_recovery",
    "groups",
    "logs",
    "orders",
    "performance",
    "routine_instances",
    "runtime",
    "stocks",
}


class GroupPackValidationError(ValueError):
    pass


@dataclass(frozen=True)
class GroupPackFile:
    source: str
    destination: str
    sha256: str


@dataclass(frozen=True)
class GroupPackInspection:
    pack_path: Path
    definition_id: str
    base_name: str
    files: tuple[GroupPackFile, ...]


@dataclass(frozen=True)
class GroupPackRegistrationResult:
    success: bool
    group: LogicalGroupRecord | None = None
    installed_files: tuple[str, ...] = ()
    reused_files: tuple[str, ...] = ()
    error_code: str = ""
    error: str = ""


def _safe_archive_path(value: object, *, field_name: str) -> PurePosixPath:
    text = str(value or "").strip()
    if not text or "\\" in text or "\0" in text:
        raise GroupPackValidationError(f"{field_name} 경로가 올바르지 않습니다.")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise GroupPackValidationError(f"{field_name} 경로가 안전하지 않습니다.")
    if path.parts and ":" in path.parts[0]:
        raise GroupPackValidationError(f"{field_name} 절대경로는 사용할 수 없습니다.")
    return path


def _safe_destination(value: object) -> PurePosixPath:
    path = _safe_archive_path(value, field_name="destination")
    if path.parts[0].casefold() in _FORBIDDEN_DESTINATION_ROOTS:
        raise GroupPackValidationError(
            f"보호된 저장 위치에는 설치할 수 없습니다: {path.as_posix()}"
        )
    return path


def validated_group_pack_destination(value: object) -> str:
    """Return one normalized, install-safe project-relative destination."""
    return _safe_destination(value).as_posix()


def _zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_IFMT((info.external_attr >> 16) & 0xFFFF) == stat.S_IFLNK


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_group_pack(
    pack_path: Path | str,
    *,
    project_root: Path | str | None = None,
) -> GroupPackInspection:
    del project_root  # Reserved for a stable public inspection signature.
    path = Path(pack_path)
    if not path.is_file() or not path.name.casefold().endswith(".group.zip"):
        raise GroupPackValidationError("*.group.zip 파일을 선택하세요.")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            names: set[str] = set()
            file_infos: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                archive_path = _safe_archive_path(info.filename, field_name="ZIP")
                normalized = archive_path.as_posix()
                if normalized in names:
                    raise GroupPackValidationError("ZIP에 중복 경로가 있습니다.")
                names.add(normalized)
                if _zip_entry_is_symlink(info):
                    raise GroupPackValidationError("심볼릭 링크는 포함할 수 없습니다.")
                if not info.is_dir():
                    file_infos[normalized] = info

            manifest_info = file_infos.get("group_pack.json")
            if manifest_info is None:
                raise GroupPackValidationError("group_pack.json이 없습니다.")
            try:
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except Exception as exc:
                raise GroupPackValidationError("group_pack.json을 읽을 수 없습니다.") from exc
            if not isinstance(manifest, dict):
                raise GroupPackValidationError("group_pack.json은 JSON 객체여야 합니다.")
            if set(manifest) != {"schema_version", "definition_id", "base_name", "files"}:
                raise GroupPackValidationError("group_pack.json에 허용되지 않은 필드가 있습니다.")
            if str(manifest.get("schema_version") or "") != GROUP_PACK_SCHEMA_VERSION:
                raise GroupPackValidationError("지원하지 않는 Group Pack schema_version입니다.")
            definition_id = str(manifest.get("definition_id") or "").strip()
            base_name = str(manifest.get("base_name") or "").strip()
            if not definition_id or not base_name:
                raise GroupPackValidationError("definition_id와 base_name이 필요합니다.")
            if any(character in base_name for character in ("/", "\\", "\0")):
                raise GroupPackValidationError("base_name이 올바르지 않습니다.")
            raw_files = manifest.get("files")
            if not isinstance(raw_files, list) or not raw_files:
                raise GroupPackValidationError("files 목록이 필요합니다.")

            files: list[GroupPackFile] = []
            sources: set[str] = set()
            destinations: set[str] = set()
            for raw_file in raw_files:
                if not isinstance(raw_file, dict):
                    raise GroupPackValidationError("files 항목이 올바르지 않습니다.")
                if set(raw_file) != {"source", "destination", "sha256"}:
                    raise GroupPackValidationError("files 항목에 허용되지 않은 필드가 있습니다.")
                source = _safe_archive_path(raw_file.get("source"), field_name="source")
                if len(source.parts) < 2 or source.parts[0] != "payload":
                    raise GroupPackValidationError("source는 payload/ 아래여야 합니다.")
                destination = _safe_destination(raw_file.get("destination"))
                digest = str(raw_file.get("sha256") or "").strip().lower()
                if not _SHA256_PATTERN.fullmatch(digest):
                    raise GroupPackValidationError("sha256 값이 올바르지 않습니다.")
                source_text = source.as_posix()
                destination_text = destination.as_posix()
                if source_text in sources or destination_text.casefold() in destinations:
                    raise GroupPackValidationError("중복 source 또는 destination이 있습니다.")
                source_info = file_infos.get(source_text)
                if source_info is None:
                    raise GroupPackValidationError(f"Pack 파일이 없습니다: {source_text}")
                if _sha256_bytes(archive.read(source_info)) != digest:
                    raise GroupPackValidationError(f"파일 해시가 일치하지 않습니다: {source_text}")
                sources.add(source_text)
                destinations.add(destination_text.casefold())
                files.append(GroupPackFile(source_text, destination_text, digest))

            if set(file_infos) != {"group_pack.json", *sources}:
                raise GroupPackValidationError("manifest에 선언되지 않은 파일이 있습니다.")
            return GroupPackInspection(path, definition_id, base_name, tuple(files))
    except zipfile.BadZipFile as exc:
        raise GroupPackValidationError("올바른 ZIP 파일이 아닙니다.") from exc


def validate_group_pack(
    pack_path: Path | str,
    *,
    project_root: Path | str | None = None,
) -> tuple[bool, str]:
    try:
        inspect_group_pack(pack_path, project_root=project_root)
        return True, ""
    except GroupPackValidationError as exc:
        return False, str(exc)


def _assert_destination_boundary(root: Path, relative_path: str) -> Path:
    destination = root.joinpath(*PurePosixPath(relative_path).parts)
    resolved_root = root.resolve()
    resolved_destination = destination.resolve(strict=False)
    try:
        resolved_destination.relative_to(resolved_root)
    except ValueError as exc:
        raise GroupPackValidationError("설치 경로가 프로젝트 범위를 벗어납니다.") from exc
    cursor = destination.parent
    while cursor != root.parent:
        if cursor.exists() and cursor.is_symlink():
            raise GroupPackValidationError("심볼릭 링크 경로에는 설치할 수 없습니다.")
        if cursor == root:
            break
        cursor = cursor.parent
    return destination


def _remove_empty_parents(path: Path, *, stop: Path) -> None:
    cursor = path
    while cursor != stop and cursor.is_dir():
        try:
            cursor.rmdir()
        except OSError:
            break
        cursor = cursor.parent


def register_group_pack(
    pack_path: Path | str,
    *,
    project_root: Path | str | None = None,
    repository: LogicalGroupRepository | None = None,
) -> GroupPackRegistrationResult:
    root = Path(project_root or PROJECT_ROOT)
    try:
        inspection = inspect_group_pack(pack_path, project_root=root)
    except GroupPackValidationError as exc:
        return GroupPackRegistrationResult(False, error_code="PACK_INVALID", error=str(exc))

    installed: list[str] = []
    reused: list[str] = []
    created_parents: list[Path] = []
    created_group_id = ""
    group_repository = repository or LogicalGroupRepository(root)
    staging_root = root / f".group-pack-{uuid4().hex}.tmp"
    try:
        destinations: dict[GroupPackFile, Path] = {}
        with zipfile.ZipFile(inspection.pack_path, "r") as archive:
            for item in inspection.files:
                destination = _assert_destination_boundary(root, item.destination)
                destinations[item] = destination
                if destination.exists():
                    if not destination.is_file():
                        raise GroupPackValidationError(
                            f"설치 대상이 파일이 아닙니다: {item.destination}"
                        )
                    if _sha256_bytes(destination.read_bytes()) != item.sha256:
                        raise GroupPackValidationError(
                            f"기존 파일과 내용이 다릅니다: {item.destination}"
                        )
                    reused.append(item.destination)
                    continue
                staged_file = staging_root.joinpath(*PurePosixPath(item.destination).parts)
                staged_file.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(item.source)
                staged_file.write_bytes(data)
                if _sha256_bytes(staged_file.read_bytes()) != item.sha256:
                    raise GroupPackValidationError(
                        f"임시 설치 검증에 실패했습니다: {item.destination}"
                    )

        for item, destination in destinations.items():
            if item.destination in reused:
                continue
            missing_parents: list[Path] = []
            cursor = destination.parent
            while cursor != root and not cursor.exists():
                missing_parents.append(cursor)
                cursor = cursor.parent
            destination.parent.mkdir(parents=True, exist_ok=True)
            created_parents.extend(reversed(missing_parents))
            staged_file = staging_root.joinpath(*PurePosixPath(item.destination).parts)
            os.replace(staged_file, destination)
            installed.append(item.destination)
            if _sha256_bytes(destination.read_bytes()) != item.sha256:
                raise GroupPackValidationError(
                    f"설치 파일 read-back 검증에 실패했습니다: {item.destination}"
                )

        definition = routine_definition_by_id(
            inspection.definition_id,
            project_root=root,
            routines_root=root / "routines",
        )
        if definition is None:
            raise GroupPackValidationError("설치한 Routine Definition을 찾을 수 없습니다.")
        definition_name = str(getattr(definition, "display_name", "") or "").strip()
        if definition_name != inspection.base_name:
            raise GroupPackValidationError(
                "Group Pack base_name이 Routine Definition 표시명과 일치하지 않습니다."
            )
        capability = validate_routine_definition_capabilities(definition)
        if capability.get("ok") is not True:
            raise GroupPackValidationError(
                "설치한 Routine capability를 확인할 수 없습니다: "
                + ", ".join(capability.get("errors", []))
            )
        created = group_repository.create_group(
            inspection.definition_id,
            inspection.base_name,
            register=True,
        )
        if not created.success or created.group is None:
            raise GroupPackValidationError(created.error or "Logical Group을 생성하지 못했습니다.")
        created_group_id = created.group.group_id
        saved = group_repository.get_group(created.group.group_id)
        registry_state = group_repository.registry_state()
        if (
            saved != created.group
            or not registry_state.valid
            or created.group.group_id not in registry_state.group_ids
        ):
            raise RuntimeError("등록된 Logical Group read-back 검증에 실패했습니다.")
        return GroupPackRegistrationResult(
            True,
            group=created.group,
            installed_files=tuple(installed),
            reused_files=tuple(reused),
        )
    except Exception as exc:
        rollback_error = ""
        if created_group_id:
            try:
                group_repository.rollback_created_group(created_group_id)
            except Exception as rollback_exc:
                rollback_error = f" Group rollback failed: {rollback_exc}"
        for relative_path in reversed(installed):
            path = root.joinpath(*PurePosixPath(relative_path).parts)
            path.unlink(missing_ok=True)
        for parent in reversed(created_parents):
            _remove_empty_parents(parent, stop=root)
        return GroupPackRegistrationResult(
            False,
            installed_files=tuple(installed),
            reused_files=tuple(reused),
            error_code="PACK_REGISTRATION_FAILED",
            error=f"{exc}{rollback_error}",
        )
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
