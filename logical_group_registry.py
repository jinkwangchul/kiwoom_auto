# -*- coding: utf-8 -*-
"""Logical UUID Group registry and atomic persistence boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from threading import RLock
from typing import Any, Callable
from uuid import UUID, uuid4

from routine_instance_registry import routine_definition_by_id


PROJECT_ROOT = Path(__file__).resolve().parent
GROUP_SCHEMA_VERSION = "1.0"
GROUP_REGISTRY_SCHEMA_VERSION = "1.0"
GROUP_REGISTRY_MODE_LOGICAL = "logical"
_CREATE_ATTEMPTS = 32
_REGISTRY_LOCK = RLock()


@dataclass(frozen=True)
class LogicalGroupRecord:
    schema_version: str
    group_id: str
    definition_id: str
    base_name: str
    display_name: str
    slot: int
    created_at: str
    group_dir: Path


@dataclass(frozen=True)
class LogicalGroupCreateResult:
    success: bool
    group: LogicalGroupRecord | None = None
    error_code: str = ""
    error: str = ""


@dataclass(frozen=True)
class LogicalGroupScanResult:
    groups: tuple[LogicalGroupRecord, ...]
    complete: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class LogicalGroupRegistryState:
    exists: bool
    valid: bool
    mode: str
    group_ids: tuple[str, ...] = ()
    cutover_at: str = ""
    error: str = ""


class LogicalGroupRepository:
    def __init__(
        self,
        project_root: Path | str | None = None,
        *,
        id_factory: Callable[[], Any] = uuid4,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.project_root = Path(project_root or PROJECT_ROOT)
        self.routines_root = self.project_root / "routines"
        self.groups_root = self.project_root / "groups"
        self.registry_path = self.groups_root / "registry.json"
        self._id_factory = id_factory
        self._now_factory = now_factory or (lambda: datetime.now().astimezone())

    def list_groups(self) -> list[LogicalGroupRecord]:
        result = self.scan_groups()
        if result.errors:
            raise ValueError(result.errors[0])
        return list(result.groups)

    def scan_groups(self) -> LogicalGroupScanResult:
        if not self.groups_root.exists():
            return LogicalGroupScanResult((), False)
        if not self.groups_root.is_dir():
            return LogicalGroupScanResult((), False, ("groups root must be a directory",))

        records: list[LogicalGroupRecord] = []
        errors: list[str] = []
        for group_dir in sorted(self.groups_root.iterdir(), key=lambda path: path.name):
            if not group_dir.is_dir() or group_dir.name.startswith("."):
                continue
            try:
                records.append(self._load_group_directory(group_dir))
            except Exception as exc:
                errors.append(str(exc))
        records.sort(
            key=lambda record: (
                record.base_name.casefold(),
                record.slot,
                record.display_name.casefold(),
                record.group_id,
            )
        )
        return LogicalGroupScanResult(
            groups=tuple(records),
            complete=bool(records) and not errors,
            errors=tuple(errors),
        )

    def get_group(self, group_id: str) -> LogicalGroupRecord | None:
        clean_group_id = self._canonical_uuid(group_id)
        if not clean_group_id:
            return None
        group_dir = self.groups_root / clean_group_id
        if not group_dir.is_dir():
            return None
        return self._load_group_directory(group_dir)

    def registry_state(self, *, path: Path | None = None) -> LogicalGroupRegistryState:
        registry_path = Path(path) if path is not None else self.registry_path
        if not registry_path.exists():
            return LogicalGroupRegistryState(False, True, "absent")
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("registry.json must contain an object")
            if str(payload.get("schema_version") or "") != GROUP_REGISTRY_SCHEMA_VERSION:
                raise ValueError("unsupported registry schema_version")
            if str(payload.get("mode") or "") != GROUP_REGISTRY_MODE_LOGICAL:
                raise ValueError("unsupported Group registry mode")
            raw_group_ids = payload.get("group_ids")
            if not isinstance(raw_group_ids, list):
                raise ValueError("registry group_ids must be a list")
            group_ids = tuple(self._canonical_uuid(value) for value in raw_group_ids)
            if any(not group_id for group_id in group_ids):
                raise ValueError("registry group_ids must contain UUID values")
            if len(group_ids) != len(set(group_ids)):
                raise ValueError("registry group_ids must be unique")
            cutover_at = str(payload.get("cutover_at") or "").strip()
            datetime.fromisoformat(cutover_at)

            scan = self.scan_groups()
            if scan.errors:
                raise ValueError(scan.errors[0])
            discovered_ids = {group.group_id for group in scan.groups}
            if discovered_ids != set(group_ids):
                raise ValueError("registry group_ids do not match logical Group storage")
            return LogicalGroupRegistryState(
                True,
                True,
                GROUP_REGISTRY_MODE_LOGICAL,
                group_ids,
                cutover_at,
            )
        except Exception as exc:
            return LogicalGroupRegistryState(True, False, "invalid", error=str(exc))

    def logical_cutover_active(self) -> bool:
        state = self.registry_state()
        return state.exists and state.valid and state.mode == GROUP_REGISTRY_MODE_LOGICAL

    def promote_logical_cutover(
        self,
        group_ids: list[str] | tuple[str, ...],
        *,
        cutover_at: str = "",
    ) -> LogicalGroupRegistryState:
        canonical_ids = tuple(self._canonical_uuid(value) for value in group_ids)
        if any(not group_id for group_id in canonical_ids):
            raise ValueError("logical cutover requires UUID group_ids")
        if len(canonical_ids) != len(set(canonical_ids)):
            raise ValueError("logical cutover group_ids must be unique")
        if self.registry_path.exists():
            raise FileExistsError("Group registry cutover already exists")
        timestamp = cutover_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = {
            "schema_version": GROUP_REGISTRY_SCHEMA_VERSION,
            "mode": GROUP_REGISTRY_MODE_LOGICAL,
            "group_ids": list(canonical_ids),
            "cutover_at": timestamp,
        }
        return self._replace_registry(payload, require_absent=True)

    def remove_group_from_registry(self, group_id: str) -> LogicalGroupRegistryState:
        target = self._canonical_uuid(group_id)
        try:
            current = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError("logical Group registry cannot be read") from exc
        if (
            not target
            or not isinstance(current, dict)
            or str(current.get("schema_version") or "") != GROUP_REGISTRY_SCHEMA_VERSION
            or str(current.get("mode") or "") != GROUP_REGISTRY_MODE_LOGICAL
            or not isinstance(current.get("group_ids"), list)
        ):
            raise ValueError("valid logical Group registry is required")
        current_ids = tuple(self._canonical_uuid(value) for value in current["group_ids"])
        if any(not value for value in current_ids) or len(current_ids) != len(set(current_ids)):
            raise ValueError("logical Group registry group_ids are invalid")
        if target not in current_ids:
            raise ValueError("logical Group is not listed in registry")
        payload = {
            "schema_version": GROUP_REGISTRY_SCHEMA_VERSION,
            "mode": GROUP_REGISTRY_MODE_LOGICAL,
            "group_ids": [value for value in current_ids if value != target],
            "cutover_at": str(current.get("cutover_at") or ""),
        }
        return self._replace_registry(payload, require_absent=False)

    def rollback_created_group(self, group_id: str) -> None:
        """Remove one just-created Group without touching any related user data."""
        target = self._canonical_uuid(group_id)
        state = self.registry_state()
        group_dir = self.groups_root / target
        if not target or not state.valid or target not in state.group_ids:
            raise ValueError("rollback target is not a registered Logical Group")
        if not group_dir.is_dir():
            raise ValueError("rollback target Group directory is missing")
        hidden_dir = self.groups_root / f".{target}.{uuid4().hex}.rollback"
        os.replace(group_dir, hidden_dir)
        try:
            payload = {
                "schema_version": GROUP_REGISTRY_SCHEMA_VERSION,
                "mode": GROUP_REGISTRY_MODE_LOGICAL,
                "group_ids": [value for value in state.group_ids if value != target],
                "cutover_at": state.cutover_at,
            }
            self._replace_registry(payload, require_absent=False)
        except Exception:
            os.replace(hidden_dir, group_dir)
            raise
        shutil.rmtree(hidden_dir)

    def validate_group_directory(
        self,
        group_dir: Path,
        *,
        expected_group_id: str = "",
    ) -> LogicalGroupRecord:
        return self._load_group_directory(group_dir, expected_group_id=expected_group_id)

    def next_available_slot(self, base_name: str) -> int:
        clean_base_name = self._clean_base_name(base_name)
        existing_names = {
            record.display_name.casefold()
            for record in self.list_groups()
        }
        slot = 0
        while self._display_name(clean_base_name, slot).casefold() in existing_names:
            slot += 1
        return slot

    def create_group(
        self,
        definition_id: str,
        base_name: str,
        *,
        register: bool = False,
    ) -> LogicalGroupCreateResult:
        clean_definition_id = str(definition_id or "").strip()
        if routine_definition_by_id(
            clean_definition_id,
            project_root=self.project_root,
            routines_root=self.routines_root,
        ) is None:
            return LogicalGroupCreateResult(
                False,
                error_code="DEFINITION_UNKNOWN",
                error="등록할 Group의 루틴 유형을 찾을 수 없습니다.",
            )
        try:
            clean_base_name = self._clean_base_name(base_name)
        except ValueError as exc:
            return LogicalGroupCreateResult(
                False,
                error_code="BASE_NAME_INVALID",
                error=str(exc),
            )

        with _REGISTRY_LOCK:
            promoted = False
            try:
                registry_state = self.registry_state()
                if register and registry_state.exists and not registry_state.valid:
                    raise ValueError(
                        registry_state.error or "Logical Group registry is invalid"
                    )
                slot = self.next_available_slot(clean_base_name)
                display_name = self._display_name(clean_base_name, slot)
                existing_names = {
                    record.display_name.casefold()
                    for record in self.list_groups()
                }
                if display_name.casefold() in existing_names:
                    raise RuntimeError("Group display_name allocation collided")

                group_id = self._new_group_id()
                final_dir = self.groups_root / group_id
                temp_dir = self.groups_root / f".{group_id}.{uuid4().hex}.tmp"
                created_at = self._now_factory().isoformat(timespec="seconds")
                metadata = {
                    "schema_version": GROUP_SCHEMA_VERSION,
                    "group_id": group_id,
                    "definition_id": clean_definition_id,
                    "base_name": clean_base_name,
                    "display_name": display_name,
                    "slot": slot,
                    "created_at": created_at,
                }

                self.groups_root.mkdir(parents=True, exist_ok=True)
                temp_dir.mkdir()
                self._write_json(temp_dir / "group.json", metadata)
                staged = self._load_group_directory(temp_dir, expected_group_id=group_id)
                if self._record_payload(staged) != metadata:
                    raise ValueError("staged group.json verification mismatch")
                os.replace(temp_dir, final_dir)
                promoted = True

                saved = self.get_group(group_id)
                if saved is None or self._record_payload(saved) != metadata:
                    raise ValueError("group.json read-back verification mismatch")
                if register:
                    existing_ids = (
                        registry_state.group_ids
                        if registry_state.exists
                        else tuple(
                            group.group_id
                            for group in self.scan_groups().groups
                            if group.group_id != group_id
                        )
                    )
                    registry_payload = {
                        "schema_version": GROUP_REGISTRY_SCHEMA_VERSION,
                        "mode": GROUP_REGISTRY_MODE_LOGICAL,
                        "group_ids": [*existing_ids, group_id],
                        "cutover_at": (
                            registry_state.cutover_at
                            if registry_state.exists
                            else datetime.now(timezone.utc).isoformat(timespec="seconds")
                        ),
                    }
                    registered = self._replace_registry(
                        registry_payload,
                        require_absent=not registry_state.exists,
                    )
                    if group_id not in registered.group_ids:
                        raise ValueError("created Group was not registered")
                return LogicalGroupCreateResult(True, group=saved)
            except Exception as exc:
                if "temp_dir" in locals() and temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
                if promoted and "final_dir" in locals() and final_dir.exists():
                    shutil.rmtree(final_dir, ignore_errors=True)
                return LogicalGroupCreateResult(
                    False,
                    error_code="GROUP_CREATE_FAILED",
                    error=str(exc),
                )

    def _new_group_id(self) -> str:
        for _attempt in range(_CREATE_ATTEMPTS):
            candidate = self._canonical_uuid(self._id_factory())
            if not candidate:
                raise ValueError("group_id factory did not return a UUID")
            if not (self.groups_root / candidate).exists():
                return candidate
        raise RuntimeError("고유한 group_id를 생성하지 못했습니다.")

    def _replace_registry(
        self,
        payload: dict[str, object],
        *,
        require_absent: bool,
    ) -> LogicalGroupRegistryState:
        self.groups_root.mkdir(parents=True, exist_ok=True)
        temp_path = self.groups_root / f".registry.{uuid4().hex}.tmp"
        try:
            self._write_json(temp_path, payload)
            staged = self.registry_state(path=temp_path)
            if not staged.valid:
                raise ValueError(staged.error or "staged registry verification failed")
            if require_absent and self.registry_path.exists():
                raise FileExistsError("Group registry cutover already exists")
            os.replace(temp_path, self.registry_path)
            saved = self.registry_state()
            if not saved.valid or saved.group_ids != staged.group_ids:
                raise ValueError(saved.error or "registry read-back verification failed")
            return saved
        finally:
            temp_path.unlink(missing_ok=True)

    def _load_group_directory(
        self,
        group_dir: Path,
        *,
        expected_group_id: str = "",
    ) -> LogicalGroupRecord:
        metadata_path = group_dir / "group.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"group.json read failed: {metadata_path}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise ValueError(f"group.json must contain an object: {metadata_path}")

        group_id = self._canonical_uuid(metadata.get("group_id"))
        directory_group_id = self._canonical_uuid(expected_group_id or group_dir.name)
        if not group_id or group_id != directory_group_id:
            raise ValueError(f"group_id does not match its directory: {metadata_path}")
        schema_version = str(metadata.get("schema_version") or "").strip()
        if schema_version != GROUP_SCHEMA_VERSION:
            raise ValueError(f"unsupported Group schema_version: {schema_version}")
        definition_id = str(metadata.get("definition_id") or "").strip()
        if not definition_id:
            raise ValueError("definition_id is required")
        base_name = self._clean_base_name(metadata.get("base_name"))
        slot = metadata.get("slot")
        if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
            raise ValueError("slot must be a non-negative integer")
        display_name = str(metadata.get("display_name") or "").strip()
        if display_name != self._display_name(base_name, slot):
            raise ValueError("display_name does not match base_name and slot")
        created_at = str(metadata.get("created_at") or "").strip()
        try:
            datetime.fromisoformat(created_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("created_at must be ISO8601") from exc

        return LogicalGroupRecord(
            schema_version=schema_version,
            group_id=group_id,
            definition_id=definition_id,
            base_name=base_name,
            display_name=display_name,
            slot=slot,
            created_at=created_at,
            group_dir=group_dir,
        )

    @staticmethod
    def _canonical_uuid(value: object) -> str:
        try:
            return str(UUID(str(value or "").strip()))
        except (AttributeError, TypeError, ValueError):
            return ""

    @staticmethod
    def _clean_base_name(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("base_name is required")
        if any(character in text for character in ("/", "\\", "\0")):
            raise ValueError("base_name contains an invalid character")
        return text

    @staticmethod
    def _display_name(base_name: str, slot: int) -> str:
        return base_name if slot == 0 else f"{base_name}_{slot}"

    @staticmethod
    def _record_payload(record: LogicalGroupRecord) -> dict[str, object]:
        return {
            "schema_version": record.schema_version,
            "group_id": record.group_id,
            "definition_id": record.definition_id,
            "base_name": record.base_name,
            "display_name": record.display_name,
            "slot": record.slot,
            "created_at": record.created_at,
        }

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
