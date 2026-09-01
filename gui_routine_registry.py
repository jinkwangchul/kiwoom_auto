# -*- coding: utf-8 -*-
"""
gui_routine_registry.py

Routine Definition package와 Logical Group Registry를 독립적으로 인식한다.

- Routine Definition: routines/<루틴명>/routine.json
- Group: groups/registry.json + groups/<UUID>/group.json
- routine.py는 존재 여부만 확인하며 직접 매매 실행하지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
ROUTINES_ROOT = PROJECT_ROOT / "routines"


@dataclass(frozen=True)
class RoutineRecord:
    name: str
    path: Path
    source_type: str  # package
    enabled: bool
    version: str
    routine_type: str
    entry_file: str
    rules_path: Path
    settings_ui: str
    metadata: dict[str, Any]
    budget: dict[str, Any]
    valid: bool
    problem: str = ""


@dataclass(frozen=True)
class GroupRecord:
    name: str = ""
    path: Path = field(default_factory=Path)
    source_type: str = ""
    budget: dict[str, Any] = field(default_factory=dict)
    valid: bool = True
    problem: str = ""
    group_id: str = ""
    definition_id: str = ""
    base_name: str = ""
    display_name: str = ""
    slot: int = 0

    def __post_init__(self) -> None:
        path = Path(self.path)
        display_name = str(self.display_name or self.name or "").strip()
        group_id = str(self.group_id or "").strip()
        if not group_id:
            raise ValueError("GroupRecord.group_id is required")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "name", display_name)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "base_name", str(self.base_name or display_name).strip())
        object.__setattr__(self, "group_id", group_id)


def _decode_hash_unicode(text: str) -> str:
    """압축/이관 과정에서 생긴 #UXXXX 표기를 사람이 읽는 문자로 복원한다."""
    def repl(match: re.Match[str]) -> str:
        try:
            return chr(int(match.group(1), 16))
        except Exception:
            return match.group(0)

    return re.sub(r"#U([0-9A-Fa-f]{4})", repl, str(text or ""))


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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


# 구형 루틴명 호환 매핑
# - 기초종목.txt, 기존 config.json 등에 남아 있는 과거 이름을
#   신규 routines/<루틴명>/routine.json 기준 이름으로 정규화한다.
_LEGACY_ROUTINE_ALIASES: dict[str, str] = {}


def _normalize_routine_name(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = _decode_hash_unicode(text).strip()

    # 원문 기준 alias 우선 처리
    if text in _LEGACY_ROUTINE_ALIASES:
        return _LEGACY_ROUTINE_ALIASES[text]

    if text.startswith("_"):
        text = text.lstrip("_").strip()

    # 언더스코어 제거 후 alias 재처리
    return _LEGACY_ROUTINE_ALIASES.get(text, text)


def normalize_routine_name(value: Any, fallback: str = "") -> str:
    """외부 모듈 호환용 공개 정규화 함수."""
    return _normalize_routine_name(value, fallback)


def _record_from_package(package_dir: Path) -> RoutineRecord | None:
    meta_path = package_dir / "routine.json"
    if not package_dir.is_dir() or not meta_path.exists():
        return None

    meta = _safe_read_json(meta_path)
    name = _normalize_routine_name(meta.get("name"), package_dir.name)
    entry_file = str(meta.get("entry_file") or "routine.py").strip() or "routine.py"
    enabled = _safe_bool(meta.get("enabled"), True)
    version = str(meta.get("version") or "").strip()
    routine_type = str(meta.get("routine_type") or "auto_trade").strip() or "auto_trade"
    rules_file = str(meta.get("rules_file") or "rules.json").strip() or "rules.json"
    settings_ui = str(meta.get("settings_ui") or "").strip()
    budget = meta.get("budget") if isinstance(meta.get("budget"), dict) else {}

    if not name:
        return RoutineRecord(
            name=_decode_hash_unicode(package_dir.name),
            path=package_dir,
            source_type="package",
            enabled=False,
            version=version,
            routine_type=routine_type,
            entry_file=entry_file,
            rules_path=package_dir / rules_file,
            settings_ui=settings_ui,
            metadata=meta,
            budget=budget,
            valid=False,
            problem="routine.json name missing",
        )

    entry_path = package_dir / entry_file
    valid = entry_path.exists() and entry_path.is_file()
    problem = "" if valid else f"entry_file missing: {entry_file}"

    return RoutineRecord(
        name=name,
        path=package_dir,
        source_type="package",
        enabled=enabled,
        version=version,
        routine_type=routine_type,
        entry_file=entry_file,
        rules_path=package_dir / rules_file,
        settings_ui=settings_ui,
        metadata=meta,
        budget=budget,
        valid=valid,
        problem=problem,
    )


def scan_routine_records(
    *,
    project_root: Path | str | None = None,
    routines_root: Path | str | None = None,
) -> list[RoutineRecord]:
    """Return Routine Definition packages only.

    Root folders are never Routine Definitions.
    """
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    if routines_root is not None:
        routine_root = Path(routines_root)
    elif project_root is not None:
        routine_root = root / "routines"
    else:
        routine_root = ROUTINES_ROOT
    records: list[RoutineRecord] = []

    if routine_root.exists() and routine_root.is_dir():
        for child in sorted(routine_root.iterdir(), key=lambda item: _decode_hash_unicode(item.name)):
            record = _record_from_package(child)
            if record is not None:
                records.append(record)

    return sorted(records, key=lambda item: item.name)


def scan_group_records(
    *,
    project_root: Path | str | None = None,
) -> list[GroupRecord]:
    """Return Groups listed by a valid logical registry."""
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    if not root.exists() or not root.is_dir():
        return []

    from logical_group_registry import LogicalGroupRepository

    logical_repository = LogicalGroupRepository(root)
    registry_state = logical_repository.registry_state()
    if not (
        registry_state.exists
        and registry_state.valid
        and registry_state.mode == "logical"
    ):
        return []
    logical_scan = logical_repository.scan_groups()
    records = [
        GroupRecord(
            name=record.display_name,
            path=record.group_dir,
            source_type="logical_registry",
            budget={},
            valid=True,
            problem="",
            group_id=record.group_id,
            definition_id=record.definition_id,
            base_name=record.base_name,
            display_name=record.display_name,
            slot=record.slot,
        )
        for record in logical_scan.groups
    ]
    return sorted(
        records,
        key=lambda item: (item.display_name.casefold(), item.group_id),
    )


def get_routine_records() -> list[RoutineRecord]:
    return scan_routine_records()


def get_group_records() -> list[GroupRecord]:
    return scan_group_records()


def group_record_by_id(
    group_id: str,
) -> GroupRecord | None:
    target = str(group_id or "").strip()
    if not target:
        return None
    for record in get_group_records():
        if record.group_id == target:
            return record
    return None


def get_group_dirs() -> list[Path]:
    return [record.path for record in get_group_records()]




def routine_display_name(routine_path: Path) -> str:
    """기존 호출부 호환용: Path에서 표시 루틴명을 반환한다."""
    path = Path(routine_path)
    if path.is_dir():
        record = _record_from_package(path)
        if record is not None:
            return record.name
    return _normalize_routine_name(path.name)


def routine_record_by_name(name: str) -> RoutineRecord | None:
    target = _normalize_routine_name(name)
    for record in get_routine_records():
        if record.name == target:
            return record
    return None


def routine_names() -> list[str]:
    return [record.name for record in get_routine_records()]


def routine_exists(name: str) -> bool:
    record = routine_record_by_name(name)
    return bool(record and record.valid and record.enabled)


def read_routine_budget(routine_path_or_name: Path | str) -> dict[str, Any]:
    """Return the budget declared by a Routine Definition package."""
    if isinstance(routine_path_or_name, Path):
        path = routine_path_or_name
        package = _record_from_package(path)
        if package is not None:
            return dict(package.budget)
        return {}

    record = routine_record_by_name(str(routine_path_or_name))
    return dict(record.budget) if record is not None else {}


def missing_routine_names(assigned_names: list[str]) -> list[str]:
    """종목 config에 지정된 루틴 중 현재 레지스트리에 없는 이름을 반환한다."""
    available = {record.name for record in get_routine_records() if record.enabled and record.valid}
    result: list[str] = []
    for item in assigned_names:
        name = _normalize_routine_name(item)
        if name and name not in available and name not in result:
            result.append(name)
    return result
