# -*- coding: utf-8 -*-
"""Persistent repository for operator-registered routine instances."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
from typing import Any, Callable
from uuid import uuid4

from routine_instance_registry import (
    RoutineInstanceRecord,
    load_persisted_routine_instances,
    routine_definition_by_id,
    routine_instance_by_id,
)


PROJECT_ROOT = Path(__file__).resolve().parent
INSTANCE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class RoutineInstanceCreateRequest:
    definition_id: str
    display_name: str
    description: str = ""
    buy_limit_enabled: bool = False
    buy_limit_amount: int | None = None


@dataclass(frozen=True)
class RoutineInstanceCreateResult:
    success: bool
    instance: RoutineInstanceRecord | None = None
    error_code: str = ""
    error: str = ""


@dataclass(frozen=True)
class RoutineInstanceRenameResult:
    success: bool
    instance: RoutineInstanceRecord | None = None
    error_code: str = ""
    error: str = ""


@dataclass(frozen=True)
class RoutineInstanceBuyLimitResult:
    success: bool
    instance: RoutineInstanceRecord | None = None
    error_code: str = ""
    error: str = ""


class RoutineInstanceRepository:
    def __init__(
        self,
        project_root: Path | str | None = None,
        *,
        id_factory: Callable[[], Any] = uuid4,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.project_root = Path(project_root or PROJECT_ROOT)
        self.routines_root = self.project_root / "routines"
        self.instances_root = self.project_root / "routine_instances"
        self._id_factory = id_factory
        self._now_factory = now_factory or (lambda: datetime.now().astimezone())

    def list_instances(self, definition_id: str | None = None) -> list[RoutineInstanceRecord]:
        records = load_persisted_routine_instances(
            project_root=self.project_root,
            routines_root=self.routines_root,
            instances_root=self.instances_root,
        )
        target = str(definition_id or "").strip()
        if not target:
            return records
        return [item for item in records if item.definition_id == target]

    def get_instance(self, instance_id: str) -> RoutineInstanceRecord | None:
        record = routine_instance_by_id(
            instance_id,
            project_root=self.project_root,
            routines_root=self.routines_root,
            instances_root=self.instances_root,
        )
        return record if record is not None and record.persisted else None

    def validate_create(self, request: RoutineInstanceCreateRequest) -> tuple[str, str]:
        definition_id = str(request.definition_id or "").strip()
        if routine_definition_by_id(
            definition_id,
            project_root=self.project_root,
            routines_root=self.routines_root,
        ) is None:
            return "DEFINITION_UNKNOWN", "등록할 루틴 유형을 찾을 수 없습니다."

        display_name = str(request.display_name or "").strip()
        if not display_name:
            return "DISPLAY_NAME_REQUIRED", "루틴 이름을 입력하세요."
        if any(
            item.display_name.casefold() == display_name.casefold()
            for item in self.list_instances(definition_id)
        ):
            return "DISPLAY_NAME_DUPLICATE", "같은 루틴 유형에 동일한 루틴 이름이 이미 있습니다."

        if not isinstance(request.buy_limit_enabled, bool):
            return "BUY_LIMIT_ENABLED_INVALID", "매수한도 활성 여부가 올바르지 않습니다."
        if request.buy_limit_enabled:
            amount = request.buy_limit_amount
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                return "BUY_LIMIT_INVALID", "매수한도는 0보다 큰 원 단위 정수여야 합니다."
        elif request.buy_limit_amount is not None:
            amount = request.buy_limit_amount
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                return "BUY_LIMIT_INVALID", "비활성 매수한도는 비워 두거나 0보다 큰 정수여야 합니다."
        return "", ""

    def create_instance(
        self,
        request: RoutineInstanceCreateRequest,
        rules: dict[str, Any],
    ) -> RoutineInstanceCreateResult:
        error_code, error = self.validate_create(request)
        if error_code:
            return RoutineInstanceCreateResult(False, error_code=error_code, error=error)
        if not isinstance(rules, dict):
            return RoutineInstanceCreateResult(
                False,
                error_code="RULES_INVALID",
                error="등록할 rules 데이터가 JSON 객체가 아닙니다.",
            )

        instance_id = str(self._id_factory()).lower()
        final_dir = self.instances_root / instance_id
        temp_dir = self.instances_root / f".{instance_id}.{uuid4().hex}.tmp"
        if final_dir.exists():
            return RoutineInstanceCreateResult(
                False,
                error_code="INSTANCE_ID_COLLISION",
                error="생성된 instance_id가 이미 존재합니다.",
            )

        now = self._now_factory().isoformat(timespec="seconds")
        metadata = {
            "schema_version": INSTANCE_SCHEMA_VERSION,
            "instance_id": instance_id,
            "definition_id": str(request.definition_id).strip(),
            "display_name": str(request.display_name).strip(),
            "description": str(request.description or "").strip(),
            "enabled": False,
            "buy_limit_enabled": request.buy_limit_enabled,
            "buy_limit_amount": request.buy_limit_amount,
            "rules_file": "rules.json",
            "created_at": now,
            "updated_at": now,
        }

        try:
            self.instances_root.mkdir(parents=True, exist_ok=True)
            temp_dir.mkdir()
            self._write_json(temp_dir / "instance.json", metadata)
            self._write_json(temp_dir / "rules.json", deepcopy(rules))
            self._verify_staged_instance(temp_dir, metadata)
            os.replace(temp_dir, final_dir)

            instance = self.get_instance(instance_id)
            if instance is None:
                raise RuntimeError("저장된 등록 루틴을 다시 읽어 검증하지 못했습니다.")
            return RoutineInstanceCreateResult(True, instance=instance)
        except Exception as exc:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            if final_dir.exists():
                shutil.rmtree(final_dir, ignore_errors=True)
            return RoutineInstanceCreateResult(
                False,
                error_code="INSTANCE_CREATE_FAILED",
                error=str(exc),
            )

    def rename_instance(
        self,
        instance_id: str,
        display_name: str,
    ) -> RoutineInstanceRenameResult:
        instance = self.get_instance(instance_id)
        if instance is None:
            return RoutineInstanceRenameResult(
                False,
                error_code="INSTANCE_UNKNOWN",
                error="변경할 등록 루틴을 찾을 수 없습니다.",
            )

        new_name = str(display_name or "").strip()
        if not new_name:
            return RoutineInstanceRenameResult(
                False,
                error_code="DISPLAY_NAME_REQUIRED",
                error="루틴 이름을 입력하세요.",
            )
        if new_name == instance.display_name:
            return RoutineInstanceRenameResult(True, instance=instance)
        if any(
            item.instance_id != instance.instance_id
            and item.definition_id == instance.definition_id
            and item.display_name.casefold() == new_name.casefold()
            for item in self.list_instances(instance.definition_id)
        ):
            return RoutineInstanceRenameResult(
                False,
                error_code="DISPLAY_NAME_DUPLICATE",
                error="같은 루틴 유형에 동일한 루틴 이름이 이미 있습니다.",
            )

        instance_dir = self.instances_root / instance.instance_id
        metadata_path = instance_dir / "instance.json"
        temp_path = instance_dir / f".instance.{uuid4().hex}.tmp"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("instance.json must contain an object")
            metadata["display_name"] = new_name
            metadata["updated_at"] = self._now_factory().isoformat(timespec="seconds")
            self._write_json_replace(temp_path, metadata)
            os.replace(temp_path, metadata_path)
            saved = json.loads(metadata_path.read_text(encoding="utf-8"))
            if saved != metadata:
                raise ValueError("instance.json 저장 후 검증이 일치하지 않습니다.")
            renamed = self.get_instance(instance.instance_id)
            if renamed is None or renamed.display_name != new_name:
                raise RuntimeError("변경된 등록 루틴을 다시 읽어 검증하지 못했습니다.")
            return RoutineInstanceRenameResult(True, instance=renamed)
        except Exception as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            return RoutineInstanceRenameResult(
                False,
                error_code="INSTANCE_RENAME_FAILED",
                error=str(exc),
            )

    def update_buy_limit(
        self,
        instance_id: str,
        *,
        enabled: bool,
        amount: int | None = None,
    ) -> RoutineInstanceBuyLimitResult:
        instance = self.get_instance(instance_id)
        if instance is None:
            return RoutineInstanceBuyLimitResult(
                False,
                error_code="INSTANCE_UNKNOWN",
                error="변경할 등록 루틴을 찾을 수 없습니다.",
            )
        if not isinstance(enabled, bool):
            return RoutineInstanceBuyLimitResult(
                False,
                error_code="BUY_LIMIT_ENABLED_INVALID",
                error="매수한도 활성 여부가 올바르지 않습니다.",
            )
        clean_amount = None
        if enabled:
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                return RoutineInstanceBuyLimitResult(
                    False,
                    error_code="BUY_LIMIT_INVALID",
                    error="매수한도는 0보다 큰 원 단위 정수여야 합니다.",
                )
            clean_amount = amount

        instance_dir = self.instances_root / instance.instance_id
        metadata_path = instance_dir / "instance.json"
        temp_path = instance_dir / f".instance.{uuid4().hex}.tmp"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("instance.json must contain an object")
            metadata["buy_limit_enabled"] = enabled
            metadata["buy_limit_amount"] = clean_amount
            metadata["updated_at"] = self._now_factory().isoformat(timespec="seconds")
            self._write_json_replace(temp_path, metadata)
            os.replace(temp_path, metadata_path)
            saved = json.loads(metadata_path.read_text(encoding="utf-8"))
            if saved != metadata:
                raise ValueError("instance.json 저장 후 검증이 일치하지 않습니다.")
            updated = self.get_instance(instance.instance_id)
            if updated is None:
                raise RuntimeError("변경된 등록 루틴을 다시 읽어 검증하지 못했습니다.")
            if (
                updated.buy_limit_enabled != enabled
                or updated.buy_limit_amount != clean_amount
            ):
                raise RuntimeError("변경된 매수한도 값이 재읽기 검증과 일치하지 않습니다.")
            return RoutineInstanceBuyLimitResult(True, instance=updated)
        except Exception as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            return RoutineInstanceBuyLimitResult(
                False,
                error_code="INSTANCE_BUY_LIMIT_UPDATE_FAILED",
                error=str(exc),
            )

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _write_json_replace(path: Path, data: dict[str, Any]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _verify_staged_instance(instance_dir: Path, expected_metadata: dict[str, Any]) -> None:
        metadata = json.loads((instance_dir / "instance.json").read_text(encoding="utf-8"))
        rules = json.loads((instance_dir / "rules.json").read_text(encoding="utf-8"))
        if metadata != expected_metadata:
            raise ValueError("instance.json 저장 후 검증이 일치하지 않습니다.")
        if not isinstance(rules, dict):
            raise ValueError("rules.json 저장 후 JSON 객체 검증에 실패했습니다.")
