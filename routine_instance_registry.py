# -*- coding: utf-8 -*-
"""Read-only routine definition/instance registry.

This module exposes a virtual RoutineInstance view over the existing
``routines/<routine>/routine.json`` packages. It does not create
``routine_instances/`` files and does not mutate legacy routine or stock data.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID


PROJECT_ROOT = Path(__file__).resolve().parent
ROUTINES_ROOT = PROJECT_ROOT / "routines"
ROUTINE_INSTANCES_ROOT = PROJECT_ROOT / "routine_instances"
LEGACY_INSTANCE_PREFIX = "legacy::"
LEGACY_INSTANCE_SOURCE = "LEGACY_ADAPTER"
PERSISTED_INSTANCE_SOURCE = "PERSISTED"
SUPPORTED_INSTANCE_SCHEMA_VERSIONS = {"1.0"}


@dataclass(frozen=True)
class RoutineDefinitionRecord:
    definition_id: str
    display_name: str
    package_dir: Path
    schema_version: str
    version: str
    routine_type: str
    entry_file: str
    module_name: str
    settings_ui: str
    default_rules_file: str
    package_enabled: bool
    source_name: str


@dataclass(frozen=True)
class RoutineInstanceRecord:
    instance_id: str
    definition_id: str
    display_name: str
    source_routine_name: str
    persisted: bool
    source: str
    enabled: bool
    real_trade_allowed: bool
    description: str = ""
    buy_limit_enabled: bool = False
    buy_limit_amount: int | None = None
    rules_path: Path | None = None
    schema_version: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class RoutineRegistryDiagnostic:
    code: str
    message: str
    path: Path | None = None
    definition_id: str = ""


@dataclass(frozen=True)
class RoutineDefinitionRegistry:
    definitions: list[RoutineDefinitionRecord]
    diagnostics: list[RoutineRegistryDiagnostic]


@dataclass(frozen=True)
class RoutineInstanceRegistry:
    instances: list[RoutineInstanceRecord]
    diagnostics: list[RoutineRegistryDiagnostic]


def _read_json(path: Path) -> tuple[dict[str, Any] | None, RoutineRegistryDiagnostic | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, RoutineRegistryDiagnostic(
            code="ROUTINE_JSON_PARSE_ERROR",
            message=f"routine.json parse failed: {exc}",
            path=path,
        )

    if not isinstance(data, dict):
        return None, RoutineRegistryDiagnostic(
            code="ROUTINE_JSON_NOT_OBJECT",
            message="routine.json must contain a JSON object",
            path=path,
        )
    return data, None


def _safe_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return default


def _canonical_uuid(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = UUID(text)
    except (ValueError, TypeError, AttributeError):
        return ""
    return str(parsed)


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if number != number.to_integral_value() or number <= 0:
        return None
    return int(number)


def _ascii_slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"_routine$", "", text)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _legacy_slug(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    slug = _ascii_slug(text)
    if slug:
        return slug
    encoded = "_".join(f"{ord(char):x}" for char in text if char.strip())
    return f"legacy_{encoded}" if encoded else ""


def _definition_id_from_metadata(meta: dict[str, Any], package_dir: Path) -> str:
    explicit = _ascii_slug(meta.get("definition_id"))
    if explicit:
        return explicit

    settings_ui = _ascii_slug(meta.get("settings_ui"))
    if settings_ui:
        return settings_ui

    module_name = _ascii_slug(meta.get("module_name"))
    if module_name:
        return module_name

    return _legacy_slug(meta.get("name") or package_dir.name)


def _definition_from_package(package_dir: Path) -> tuple[RoutineDefinitionRecord | None, RoutineRegistryDiagnostic | None]:
    meta_path = package_dir / "routine.json"
    if not package_dir.is_dir():
        return None, RoutineRegistryDiagnostic(
            code="PACKAGE_DIRECTORY_MISSING",
            message="routine package directory is missing",
            path=package_dir,
        )
    if not meta_path.exists():
        return None, RoutineRegistryDiagnostic(
            code="ROUTINE_JSON_MISSING",
            message="routine.json is missing",
            path=package_dir,
        )

    meta, diagnostic = _read_json(meta_path)
    if diagnostic is not None:
        return None, diagnostic
    if meta is None:
        return None, RoutineRegistryDiagnostic(
            code="ROUTINE_JSON_INVALID",
            message="routine.json could not be read",
            path=meta_path,
        )

    display_name = str(meta.get("name") or package_dir.name).strip()
    definition_id = _definition_id_from_metadata(meta, package_dir)
    if not display_name:
        return None, RoutineRegistryDiagnostic(
            code="DISPLAY_NAME_MISSING",
            message="routine display name is missing",
            path=meta_path,
        )
    if not definition_id:
        return None, RoutineRegistryDiagnostic(
            code="DEFINITION_ID_MISSING",
            message="definition_id could not be derived",
            path=meta_path,
        )

    return RoutineDefinitionRecord(
        definition_id=definition_id,
        display_name=display_name,
        package_dir=package_dir,
        schema_version=str(meta.get("schema_version") or "").strip(),
        version=str(meta.get("version") or "").strip(),
        routine_type=str(meta.get("routine_type") or "auto_trade").strip() or "auto_trade",
        entry_file=str(meta.get("entry_file") or "routine.py").strip() or "routine.py",
        module_name=str(meta.get("module_name") or "").strip(),
        settings_ui=str(meta.get("settings_ui") or "").strip(),
        default_rules_file=str(meta.get("rules_file") or "rules.json").strip() or "rules.json",
        package_enabled=_safe_bool(meta.get("enabled"), True),
        source_name=display_name,
    ), None


def load_routine_definition_registry(
    *,
    project_root: Path | str | None = None,
    routines_root: Path | str | None = None,
) -> RoutineDefinitionRegistry:
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    routine_root = Path(routines_root) if routines_root is not None else root / "routines"
    diagnostics: list[RoutineRegistryDiagnostic] = []
    candidates: list[RoutineDefinitionRecord] = []

    if not routine_root.exists() or not routine_root.is_dir():
        return RoutineDefinitionRegistry(
            definitions=[],
            diagnostics=[
                RoutineRegistryDiagnostic(
                    code="ROUTINES_ROOT_MISSING",
                    message="routines root is missing",
                    path=routine_root,
                )
            ],
        )

    for package_dir in sorted(routine_root.iterdir(), key=lambda path: path.name):
        if not package_dir.is_dir():
            continue
        definition, diagnostic = _definition_from_package(package_dir)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
        if definition is not None:
            candidates.append(definition)

    by_id: dict[str, list[RoutineDefinitionRecord]] = {}
    for definition in candidates:
        by_id.setdefault(definition.definition_id, []).append(definition)

    definitions: list[RoutineDefinitionRecord] = []
    duplicate_ids = {definition_id for definition_id, items in by_id.items() if len(items) > 1}
    for definition_id in sorted(duplicate_ids):
        paths = ", ".join(str(item.package_dir) for item in by_id[definition_id])
        diagnostics.append(
            RoutineRegistryDiagnostic(
                code="DEFINITION_ID_DUPLICATE",
                message=f"duplicate definition_id: {definition_id} ({paths})",
                definition_id=definition_id,
            )
        )

    for definition in candidates:
        if definition.definition_id in duplicate_ids:
            continue
        definitions.append(definition)

    definitions.sort(key=lambda item: item.definition_id)
    return RoutineDefinitionRegistry(definitions=definitions, diagnostics=diagnostics)


def load_routine_definitions(
    *,
    project_root: Path | str | None = None,
    routines_root: Path | str | None = None,
) -> list[RoutineDefinitionRecord]:
    return load_routine_definition_registry(
        project_root=project_root,
        routines_root=routines_root,
    ).definitions


def _instance_diagnostic(code: str, message: str, path: Path, definition_id: str = "") -> RoutineRegistryDiagnostic:
    return RoutineRegistryDiagnostic(
        code=code,
        message=message,
        path=path,
        definition_id=definition_id,
    )


def _persisted_instance_from_directory(
    instance_dir: Path,
    definitions_by_id: dict[str, RoutineDefinitionRecord],
) -> tuple[RoutineInstanceRecord | None, RoutineRegistryDiagnostic | None]:
    metadata_path = instance_dir / "instance.json"
    if not metadata_path.exists():
        return None, _instance_diagnostic(
            "INSTANCE_JSON_MISSING",
            "instance.json is missing",
            instance_dir,
        )

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, _instance_diagnostic(
            "INSTANCE_JSON_PARSE_ERROR",
            f"instance.json parse failed: {exc}",
            metadata_path,
        )
    if not isinstance(metadata, dict):
        return None, _instance_diagnostic(
            "INSTANCE_JSON_NOT_OBJECT",
            "instance.json must contain a JSON object",
            metadata_path,
        )

    schema_version = str(metadata.get("schema_version") or "").strip()
    if schema_version not in SUPPORTED_INSTANCE_SCHEMA_VERSIONS:
        return None, _instance_diagnostic(
            "INSTANCE_SCHEMA_UNSUPPORTED",
            f"unsupported instance schema_version: {schema_version or '<missing>'}",
            metadata_path,
        )

    instance_id = _canonical_uuid(metadata.get("instance_id"))
    directory_id = _canonical_uuid(instance_dir.name)
    if not instance_id or not directory_id or instance_id != directory_id:
        return None, _instance_diagnostic(
            "INSTANCE_ID_INVALID",
            "instance_id must be a canonical UUID matching its directory name",
            metadata_path,
        )

    definition_id = str(metadata.get("definition_id") or "").strip()
    definition = definitions_by_id.get(definition_id)
    if definition is None:
        return None, _instance_diagnostic(
            "INSTANCE_DEFINITION_UNKNOWN",
            f"definition_id is not registered: {definition_id or '<missing>'}",
            metadata_path,
            definition_id,
        )

    display_name = str(metadata.get("display_name") or "").strip()
    if not display_name:
        return None, _instance_diagnostic(
            "INSTANCE_DISPLAY_NAME_MISSING",
            "display_name is required",
            metadata_path,
            definition_id,
        )

    enabled = metadata.get("enabled")
    buy_limit_enabled = metadata.get("buy_limit_enabled")
    if not isinstance(enabled, bool) or not isinstance(buy_limit_enabled, bool):
        return None, _instance_diagnostic(
            "INSTANCE_BOOLEAN_INVALID",
            "enabled and buy_limit_enabled must be JSON booleans",
            metadata_path,
            definition_id,
        )

    raw_buy_limit = metadata.get("buy_limit_amount")
    buy_limit_amount = _positive_integer(raw_buy_limit)
    if buy_limit_enabled and buy_limit_amount is None:
        return None, _instance_diagnostic(
            "INSTANCE_BUY_LIMIT_INVALID",
            "enabled buy limit must be a positive integer",
            metadata_path,
            definition_id,
        )
    if not buy_limit_enabled and raw_buy_limit is not None and buy_limit_amount is None:
        return None, _instance_diagnostic(
            "INSTANCE_BUY_LIMIT_INVALID",
            "disabled buy limit must be null or a positive integer",
            metadata_path,
            definition_id,
        )

    rules_file = str(metadata.get("rules_file") or "").strip()
    rules_relative = Path(rules_file)
    if (
        not rules_file
        or rules_relative.is_absolute()
        or len(rules_relative.parts) != 1
        or rules_relative.name != rules_file
    ):
        return None, _instance_diagnostic(
            "INSTANCE_RULES_PATH_INVALID",
            "rules_file must be a filename relative to the instance directory",
            metadata_path,
            definition_id,
        )
    rules_path = instance_dir / rules_relative
    try:
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, _instance_diagnostic(
            "INSTANCE_RULES_MISSING",
            "instance rules file is missing",
            rules_path,
            definition_id,
        )
    except Exception as exc:
        return None, _instance_diagnostic(
            "INSTANCE_RULES_PARSE_ERROR",
            f"instance rules parse failed: {exc}",
            rules_path,
            definition_id,
        )
    if not isinstance(rules, dict):
        return None, _instance_diagnostic(
            "INSTANCE_RULES_NOT_OBJECT",
            "instance rules must contain a JSON object",
            rules_path,
            definition_id,
        )

    return RoutineInstanceRecord(
        instance_id=instance_id,
        definition_id=definition_id,
        display_name=display_name,
        source_routine_name=definition.source_name,
        persisted=True,
        source=PERSISTED_INSTANCE_SOURCE,
        enabled=enabled,
        real_trade_allowed=False,
        description=str(metadata.get("description") or "").strip(),
        buy_limit_enabled=buy_limit_enabled,
        buy_limit_amount=buy_limit_amount,
        rules_path=rules_path,
        schema_version=schema_version,
        created_at=str(metadata.get("created_at") or "").strip(),
        updated_at=str(metadata.get("updated_at") or "").strip(),
    ), None


def _load_persisted_instances(
    instances_root: Path,
    definitions: list[RoutineDefinitionRecord],
) -> tuple[list[RoutineInstanceRecord], list[RoutineRegistryDiagnostic]]:
    if not instances_root.exists():
        return [], []
    if not instances_root.is_dir():
        return [], [
            _instance_diagnostic(
                "INSTANCES_ROOT_NOT_DIRECTORY",
                "routine_instances root must be a directory",
                instances_root,
            )
        ]

    definitions_by_id = {item.definition_id: item for item in definitions}
    instances: list[RoutineInstanceRecord] = []
    diagnostics: list[RoutineRegistryDiagnostic] = []
    for instance_dir in sorted(instances_root.iterdir(), key=lambda path: path.name):
        if not instance_dir.is_dir():
            continue
        instance, diagnostic = _persisted_instance_from_directory(instance_dir, definitions_by_id)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
        if instance is not None:
            instances.append(instance)

    duplicate_names: dict[tuple[str, str], list[RoutineInstanceRecord]] = {}
    for instance in instances:
        key = (instance.definition_id, instance.display_name.casefold())
        duplicate_names.setdefault(key, []).append(instance)
    duplicate_ids = {
        item.instance_id
        for items in duplicate_names.values()
        if len(items) > 1
        for item in items
    }
    for items in duplicate_names.values():
        if len(items) <= 1:
            continue
        diagnostics.append(
            _instance_diagnostic(
                "INSTANCE_DISPLAY_NAME_DUPLICATE",
                f"duplicate display_name within definition: {items[0].display_name}",
                items[0].rules_path.parent if items[0].rules_path is not None else instances_root,
                items[0].definition_id,
            )
        )
    instances = [item for item in instances if item.instance_id not in duplicate_ids]
    return instances, diagnostics


def load_routine_instance_registry(
    *,
    project_root: Path | str | None = None,
    routines_root: Path | str | None = None,
    instances_root: Path | str | None = None,
) -> RoutineInstanceRegistry:
    registry = load_routine_definition_registry(
        project_root=project_root,
        routines_root=routines_root,
    )
    legacy_instances = [
        RoutineInstanceRecord(
            instance_id=f"{LEGACY_INSTANCE_PREFIX}{definition.definition_id}",
            definition_id=definition.definition_id,
            display_name=definition.display_name,
            source_routine_name=definition.source_name,
            persisted=False,
            source=LEGACY_INSTANCE_SOURCE,
            enabled=definition.package_enabled,
            real_trade_allowed=False,
        )
        for definition in registry.definitions
    ]

    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    persistent_root = Path(instances_root) if instances_root is not None else root / "routine_instances"
    persisted_instances, persisted_diagnostics = _load_persisted_instances(
        persistent_root,
        registry.definitions,
    )
    instances = legacy_instances + persisted_instances

    by_id: dict[str, int] = {}
    diagnostics = list(registry.diagnostics) + persisted_diagnostics
    for instance in instances:
        by_id[instance.instance_id] = by_id.get(instance.instance_id, 0) + 1
    for instance_id, count in sorted(by_id.items()):
        if count > 1:
            diagnostics.append(
                RoutineRegistryDiagnostic(
                    code="INSTANCE_ID_DUPLICATE",
                    message=f"duplicate instance_id: {instance_id}",
                )
            )

    return RoutineInstanceRegistry(instances=instances, diagnostics=diagnostics)


def load_routine_instances(
    *,
    project_root: Path | str | None = None,
    routines_root: Path | str | None = None,
    instances_root: Path | str | None = None,
) -> list[RoutineInstanceRecord]:
    return load_routine_instance_registry(
        project_root=project_root,
        routines_root=routines_root,
        instances_root=instances_root,
    ).instances


def load_persisted_routine_instances(
    *,
    project_root: Path | str | None = None,
    routines_root: Path | str | None = None,
    instances_root: Path | str | None = None,
) -> list[RoutineInstanceRecord]:
    return [
        item
        for item in load_routine_instances(
            project_root=project_root,
            routines_root=routines_root,
            instances_root=instances_root,
        )
        if item.persisted
    ]


def routine_definition_by_id(
    definition_id: str,
    *,
    project_root: Path | str | None = None,
    routines_root: Path | str | None = None,
) -> RoutineDefinitionRecord | None:
    target = str(definition_id or "").strip()
    for definition in load_routine_definitions(project_root=project_root, routines_root=routines_root):
        if definition.definition_id == target:
            return definition
    return None


def routine_instance_by_id(
    instance_id: str,
    *,
    project_root: Path | str | None = None,
    routines_root: Path | str | None = None,
    instances_root: Path | str | None = None,
) -> RoutineInstanceRecord | None:
    target = str(instance_id or "").strip()
    for instance in load_routine_instances(
        project_root=project_root,
        routines_root=routines_root,
        instances_root=instances_root,
    ):
        if instance.instance_id == target:
            return instance
    return None
