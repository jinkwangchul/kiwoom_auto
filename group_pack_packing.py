# -*- coding: utf-8 -*-
"""Developer-only source inspection and deterministic Group Pack creation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from uuid import uuid4
import zipfile

from distribution_profile import group_packing_enabled
from group_pack_registration import (
    GROUP_PACK_SCHEMA_VERSION,
    GroupPackValidationError,
    inspect_group_pack,
    validated_group_pack_destination,
)
from logical_group_registry import LogicalGroupRecord, LogicalGroupRepository
from routine_instance_registry import RoutineDefinitionRecord, routine_definition_by_id


PROJECT_ROOT = Path(__file__).resolve().parent
GROUP_PACK_SPEC_NAME = "group_pack_spec.json"
_SPEC_FIELDS = {"definition_id", "base_name", "files"}
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class GroupPackSourceFile:
    relative_path: str
    absolute_path: Path
    sha256: str


@dataclass(frozen=True)
class GroupPackSourceInspection:
    group: LogicalGroupRecord
    definition: RoutineDefinitionRecord
    spec_path: Path
    definition_id: str
    base_name: str
    files: tuple[GroupPackSourceFile, ...]


@dataclass(frozen=True)
class GroupPackPackingResult:
    success: bool
    output_path: Path | None = None
    inspection: GroupPackSourceInspection | None = None
    error_code: str = ""
    error: str = ""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_source_boundary(root: Path, relative_path: str) -> Path:
    destination = validated_group_pack_destination(relative_path)
    path = root.joinpath(*PurePosixPath(destination).parts)
    resolved_root = root.resolve()
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise GroupPackValidationError("패킹 원본이 프로젝트 범위를 벗어납니다.") from exc
    cursor = path
    while cursor != root:
        if cursor.exists() and cursor.is_symlink():
            raise GroupPackValidationError("심볼릭 링크 파일은 패킹할 수 없습니다.")
        cursor = cursor.parent
    if not path.is_file():
        raise GroupPackValidationError(f"패킹 원본 파일이 없습니다: {destination}")
    return path


def inspect_group_pack_source(
    group_id: str,
    *,
    project_root: Path | str | None = None,
) -> GroupPackSourceInspection:
    root = Path(project_root or PROJECT_ROOT)
    group = LogicalGroupRepository(root).get_group(group_id)
    if group is None:
        raise GroupPackValidationError("선택한 Logical Group을 찾을 수 없습니다.")
    definition = routine_definition_by_id(
        group.definition_id,
        project_root=root,
        routines_root=root / "routines",
    )
    if definition is None:
        raise GroupPackValidationError("선택한 Group의 Routine Definition을 찾을 수 없습니다.")
    spec_path = definition.package_dir / GROUP_PACK_SPEC_NAME
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GroupPackValidationError(f"Group Pack spec을 읽을 수 없습니다: {spec_path}") from exc
    if not isinstance(spec, dict) or set(spec) != _SPEC_FIELDS:
        raise GroupPackValidationError("Group Pack spec 필드가 올바르지 않습니다.")
    definition_id = str(spec.get("definition_id") or "").strip()
    base_name = str(spec.get("base_name") or "").strip()
    raw_files = spec.get("files")
    if definition_id != group.definition_id or definition_id != definition.definition_id:
        raise GroupPackValidationError("Group Pack spec definition_id가 선택 Group과 다릅니다.")
    if base_name != group.base_name or base_name != definition.display_name:
        raise GroupPackValidationError("Group Pack spec base_name이 Definition과 다릅니다.")
    if not isinstance(raw_files, list) or not raw_files:
        raise GroupPackValidationError("Group Pack spec files 목록이 필요합니다.")

    files: list[GroupPackSourceFile] = []
    seen: set[str] = set()
    for raw_path in raw_files:
        relative_path = validated_group_pack_destination(raw_path)
        key = relative_path.casefold()
        if key in seen:
            raise GroupPackValidationError("Group Pack spec에 중복 파일이 있습니다.")
        seen.add(key)
        absolute_path = _assert_source_boundary(root, relative_path)
        files.append(
            GroupPackSourceFile(
                relative_path=relative_path,
                absolute_path=absolute_path,
                sha256=_sha256_file(absolute_path),
            )
        )
    files.sort(key=lambda item: item.relative_path.casefold())
    return GroupPackSourceInspection(
        group=group,
        definition=definition,
        spec_path=spec_path,
        definition_id=definition_id,
        base_name=base_name,
        files=tuple(files),
    )


def validate_group_pack_source(
    group_id: str,
    *,
    project_root: Path | str | None = None,
) -> tuple[bool, str]:
    try:
        inspect_group_pack_source(group_id, project_root=project_root)
        return True, ""
    except GroupPackValidationError as exc:
        return False, str(exc)


def _write_zip_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


def pack_group(
    group_id: str,
    output_path: Path | str,
    *,
    project_root: Path | str | None = None,
) -> GroupPackPackingResult:
    if not group_packing_enabled():
        return GroupPackPackingResult(
            False,
            error_code="DEVELOPER_PROFILE_REQUIRED",
            error="그룹패킹은 developer 배포 Profile에서만 사용할 수 있습니다.",
        )
    root = Path(project_root or PROJECT_ROOT)
    output = Path(output_path)
    if not output.name.casefold().endswith(".group.zip"):
        return GroupPackPackingResult(
            False,
            error_code="OUTPUT_NAME_INVALID",
            error="출력 파일명은 *.group.zip 형식이어야 합니다.",
        )
    try:
        project_relative_output = output.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        pass
    else:
        try:
            validated_group_pack_destination(project_relative_output.as_posix())
        except GroupPackValidationError as exc:
            return GroupPackPackingResult(
                False,
                error_code="OUTPUT_PATH_PROTECTED",
                error=str(exc),
            )
    temp_path = output.parent / f".{output.name}.{uuid4().hex}.tmp.group.zip"
    try:
        inspection = inspect_group_pack_source(group_id, project_root=root)
        manifest = {
            "schema_version": GROUP_PACK_SCHEMA_VERSION,
            "definition_id": inspection.definition_id,
            "base_name": inspection.base_name,
            "files": [
                {
                    "source": f"payload/{item.relative_path}",
                    "destination": item.relative_path,
                    "sha256": item.sha256,
                }
                for item in inspection.files
            ],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(temp_path, "w") as archive:
            _write_zip_entry(
                archive,
                "group_pack.json",
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
            for item in inspection.files:
                _write_zip_entry(
                    archive,
                    f"payload/{item.relative_path}",
                    item.absolute_path.read_bytes(),
                )

        packed = inspect_group_pack(temp_path, project_root=root)
        if (
            packed.definition_id != inspection.definition_id
            or packed.base_name != inspection.base_name
            or tuple(file.destination for file in packed.files)
            != tuple(item.relative_path for item in inspection.files)
        ):
            raise GroupPackValidationError("생성된 Group Pack read-back 검증에 실패했습니다.")
        os.replace(temp_path, output)
        return GroupPackPackingResult(True, output_path=output, inspection=inspection)
    except Exception as exc:
        return GroupPackPackingResult(
            False,
            error_code="GROUP_PACKING_FAILED",
            error=str(exc),
        )
    finally:
        temp_path.unlink(missing_ok=True)
