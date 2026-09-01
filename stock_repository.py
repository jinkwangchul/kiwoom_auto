# -*- coding: utf-8 -*-
"""
stock_repository.py

중앙 종목관리 계층 1차 적용 파일.

목적:
- 기초종목.txt 제거와 stocks/ 중앙 종목폴더 일원화를 위한 단일 접근 레이어.
- 1차 적용에서는 기존 기능을 깨지 않기 위해 gui_stock_data.read_base_stocks()가
  stocks/ 중앙 폴더가 존재할 때만 이 계층을 사용한다.

최종 목표 구조:
kiwoom_auto/
  stocks/
    005930_삼성전자/
      state.json
      config.json
      orders.json
      logs/

역할 분리:
- stocks/종목/state.json  = 종목 현재 상태의 진실
- stocks/종목/config.json = 종목 운영 설정 및 루틴 연결
- stocks/종목/orders.json = 주문 runtime
- stocks/종목/logs/       = 과거 이력
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from stock_code_contract import (
    is_broker_action_stock_code,
    is_valid_stock_code as canonical_is_valid_stock_code,
    normalize_stock_code as canonical_normalize_stock_code,
)
from routine_instance_registry import load_persisted_routine_instances

from event_journal_production import append_production_event


PROJECT_ROOT = Path(__file__).resolve().parent
STOCKS_DIR = PROJECT_ROOT / "stocks"
ROUTINE_ASSIGNMENT_HISTORY_KEY = "routine_assignment_history"

STOCK_CONFIG_WRITE_OK = "OK"
STOCK_CONFIG_WRITE_NO_CHANGE = "NO_CHANGE"
STOCK_CONFIG_WRITE_CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
STOCK_CONFIG_WRITE_INVALID_CONFIG = "INVALID_CONFIG"
STOCK_CONFIG_WRITE_INVALID_PATCH = "INVALID_PATCH"
STOCK_CONFIG_WRITE_INVALID_STOCK_IDENTITY = "INVALID_STOCK_IDENTITY"
STOCK_CONFIG_WRITE_FIELD_CONFLICT = "FIELD_CONFLICT"
STOCK_CONFIG_WRITE_ATOMIC_WRITE_FAILED = "ATOMIC_WRITE_FAILED"
STOCK_CONFIG_WRITE_READBACK_FAILED = "READBACK_FAILED"
STOCK_CONFIG_WRITE_CONCURRENT_UPDATE_RETRY_EXHAUSTED = (
    "CONCURRENT_UPDATE_RETRY_EXHAUSTED"
)

_STOCK_CONFIG_WRITE_LOCK = threading.RLock()
_STOCK_CONFIG_WRITE_MAX_RETRIES = 3


class _MissingStockConfigField:
    pass


STOCK_CONFIG_EXPECTED_MISSING = _MissingStockConfigField()


class _DeleteStockConfigField:
    pass


STOCK_CONFIG_DELETE_FIELD = _DeleteStockConfigField()


@dataclass(frozen=True)
class StockConfigWriteResult:
    ok: bool
    changed: bool
    field_keys: tuple[str, ...]
    conflict_detected: bool
    read_back_verified: bool
    reason_code: str
    before_fingerprint: str = ""
    after_fingerprint: str = ""


@dataclass(frozen=True)
class StockAssignmentMutationResult:
    ok: bool
    changed: bool = False
    reason_code: str = ""
    transaction_id: str = ""
    assignment_before: str = ""
    assignment_after: str = ""
    reconciliation_required: bool = False
    error: str = ""

    @property
    def success(self) -> bool:
        return self.ok

    @property
    def error_code(self) -> str:
        return self.reason_code

    @property
    def rollback_complete(self) -> bool:
        return not self.reconciliation_required


@dataclass(frozen=True)
class _StockConfigSnapshot:
    config: dict[str, Any] | None
    fingerprint: str
    reason_code: str


class _StockConfigConcurrentUpdateError(RuntimeError):
    pass


def _stock_config_fingerprint(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_values_equal(left: Any, right: Any) -> bool:
    return json.dumps(
        left,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) == json.dumps(
        right,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _routine_assignment(config: dict[str, Any]) -> dict[str, str]:
    return {
        "routine": str(
            config.get("routine_instance_name")
            or config.get("routine_name")
            or config.get("routine")
            or ""
        ).strip(),
        "routine_instance_id": str(
            config.get("assigned_routine_instance_id") or ""
        ).strip(),
    }


def _append_routine_changed(
    *,
    code: str,
    name: str,
    before: dict[str, str],
    after: dict[str, str],
) -> None:
    changes = [
        {"field_key": key, "before": before.get(key, ""), "after": after.get(key, "")}
        for key in ("routine", "routine_instance_id")
        if before.get(key, "") != after.get(key, "")
    ]
    if not changes:
        return
    before_instance_id = before.get("routine_instance_id", "")
    after_instance_id = after.get("routine_instance_id", "")
    operation = (
        "UNASSIGN"
        if before_instance_id and not after_instance_id
        else "ASSIGN"
        if not before_instance_id and after_instance_id
        else "CHANGE"
    )
    append_production_event(
        "ROUTINE_CHANGED",
        result="SUCCESS",
        source="STOCK_REPOSITORY",
        template_args={"stock_name": str(name or code).strip()},
        target_type="STOCK",
        target_id=str(code or "").strip(),
        target_name=str(name or "").strip(),
        stock_code=str(code or "").strip(),
        stock_name=str(name or "").strip(),
        routine=after.get("routine", ""),
        changes=changes,
        operation=operation,
        details={
            "operation": operation,
            "reason": "OPERATOR_REQUEST" if operation == "UNASSIGN" else "ASSIGNMENT_UPDATE",
            "before_instance_id": before_instance_id or None,
            "after_instance_id": after_instance_id or None,
            "before_routine": before.get("routine", "") or None,
            "after_routine": after.get("routine", "") or None,
        },
    )


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_stock_code(code: str) -> str:
    return canonical_normalize_stock_code(code)


def is_valid_stock_code(code: str) -> bool:
    return canonical_is_valid_stock_code(code)


def safe_stock_folder_name(code: str, name: str) -> str:
    clean_code = normalize_stock_code(code)
    clean_name = str(name or "").strip()
    clean_name = re.sub(r'[\\/:*?"<>|]+', "_", clean_name)
    clean_name = clean_name.replace("\n", " ").replace("\r", " ").strip()
    return f"{clean_code}_{clean_name}" if clean_name else clean_code


def read_json_dict(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_json_dict(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class StockRecord:
    """
    중앙 종목관리에서 반환하는 표준 종목 정보.

    주의:
    - holding_qty, avg_price 같은 현재 상태값은 여기에 넣지 않는다.
    - 현재 상태의 진실은 각 종목 state.json이다.
    """
    code: str
    name: str
    routine: str
    enabled: bool
    stock_path: str
    assigned_routine_instance_id: str = ""
    routine_instance_name: str = ""
    routine_definition_id: str = ""
    routine_type: str = ""

    def to_base_stock_dict(self) -> dict[str, Any]:
        routines = [self.routine] if self.routine else []
        return {
            "code": self.code,
            "name": self.name,
            "routines": routines,
            "registered_at": "-",
            "validation_status": "정상",
            "stock_path": self.stock_path,
            "enabled": self.enabled,
            "assigned_routine_instance_id": self.assigned_routine_instance_id,
            "routine_instance_name": self.routine_instance_name,
            "routine_definition_id": self.routine_definition_id,
            "routine_type": self.routine_type,
        }


@dataclass(frozen=True)
class RealtimeMonitoringUniverseProjection:
    target_stock_codes: tuple[str, ...]
    unsupported_stock_codes: tuple[str, ...]
    source_record_count: int


class StockRepository:
    """
    중앙 stocks/ 종목관리 레이어.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = Path(project_root or PROJECT_ROOT)
        self.stocks_dir = self.project_root / "stocks"
        self.last_assignment_linkage_result = None
        self.last_assignment_transaction_result: StockAssignmentMutationResult | None = None

    @staticmethod
    def _read_stock_config_snapshot(config_path: Path) -> _StockConfigSnapshot:
        try:
            payload = config_path.read_bytes()
        except FileNotFoundError:
            return _StockConfigSnapshot(
                config=None,
                fingerprint="",
                reason_code=STOCK_CONFIG_WRITE_CONFIG_NOT_FOUND,
            )
        except OSError:
            return _StockConfigSnapshot(
                config=None,
                fingerprint="",
                reason_code=STOCK_CONFIG_WRITE_INVALID_CONFIG,
            )

        fingerprint = _stock_config_fingerprint(payload)
        try:
            config = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _StockConfigSnapshot(
                config=None,
                fingerprint=fingerprint,
                reason_code=STOCK_CONFIG_WRITE_INVALID_CONFIG,
            )
        if not isinstance(config, dict):
            return _StockConfigSnapshot(
                config=None,
                fingerprint=fingerprint,
                reason_code=STOCK_CONFIG_WRITE_INVALID_CONFIG,
            )
        return _StockConfigSnapshot(
            config=config,
            fingerprint=fingerprint,
            reason_code=STOCK_CONFIG_WRITE_OK,
        )

    @staticmethod
    def _atomic_write_stock_config(
        config_path: Path,
        config: dict[str, Any],
        *,
        expected_fingerprint: str,
    ) -> None:
        temporary = config_path.with_name(
            f".{config_path.name}.{uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(config, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            try:
                current_fingerprint = _stock_config_fingerprint(
                    config_path.read_bytes()
                )
            except OSError as exc:
                raise _StockConfigConcurrentUpdateError from exc
            if current_fingerprint != expected_fingerprint:
                raise _StockConfigConcurrentUpdateError
            os.replace(temporary, config_path)
        finally:
            temporary.unlink(missing_ok=True)

    def patch_stock_config(
        self,
        code: str,
        patch: dict[str, Any],
        *,
        name: str = "",
        expected_fields: dict[str, Any] | None = None,
    ) -> StockConfigWriteResult:
        """Patch only the requested fields in the latest Stock config document."""

        field_keys = tuple(patch.keys()) if isinstance(patch, dict) else ()
        if not is_valid_stock_code(normalize_stock_code(code)):
            return StockConfigWriteResult(
                ok=False,
                changed=False,
                field_keys=field_keys,
                conflict_detected=False,
                read_back_verified=False,
                reason_code=STOCK_CONFIG_WRITE_INVALID_STOCK_IDENTITY,
            )
        if (
            not isinstance(patch, dict)
            or not patch
            or any(not isinstance(key, str) or not key.strip() for key in patch)
            or (
                expected_fields is not None
                and (
                    not isinstance(expected_fields, dict)
                    or any(
                        not isinstance(key, str)
                        or not key.strip()
                        or key not in patch
                        for key in expected_fields
                    )
                )
            )
        ):
            return StockConfigWriteResult(
                ok=False,
                changed=False,
                field_keys=field_keys,
                conflict_detected=False,
                read_back_verified=False,
                reason_code=STOCK_CONFIG_WRITE_INVALID_PATCH,
            )
        try:
            json.dumps(
                {
                    key: value
                    for key, value in patch.items()
                    if value is not STOCK_CONFIG_DELETE_FIELD
                },
                ensure_ascii=False,
            )
            if expected_fields is not None:
                json.dumps(
                    {
                        key: value
                        for key, value in expected_fields.items()
                        if value is not STOCK_CONFIG_EXPECTED_MISSING
                    },
                    ensure_ascii=False,
                )
        except (TypeError, ValueError):
            return StockConfigWriteResult(
                ok=False,
                changed=False,
                field_keys=field_keys,
                conflict_detected=False,
                read_back_verified=False,
                reason_code=STOCK_CONFIG_WRITE_INVALID_PATCH,
            )

        config_path = self.resolve_stock_dir(code, name) / "config.json"
        before_fingerprint = ""
        with _STOCK_CONFIG_WRITE_LOCK:
            for _attempt in range(_STOCK_CONFIG_WRITE_MAX_RETRIES):
                snapshot = self._read_stock_config_snapshot(config_path)
                before_fingerprint = snapshot.fingerprint
                if snapshot.config is None:
                    return StockConfigWriteResult(
                        ok=False,
                        changed=False,
                        field_keys=field_keys,
                        conflict_detected=False,
                        read_back_verified=False,
                        reason_code=snapshot.reason_code,
                        before_fingerprint=before_fingerprint,
                    )
                current = snapshot.config
                if expected_fields is not None and any(
                    (
                        expected_value is STOCK_CONFIG_EXPECTED_MISSING
                        and key in current
                    )
                    or (
                        expected_value is not STOCK_CONFIG_EXPECTED_MISSING
                        and (
                            key not in current
                            or not _json_values_equal(current[key], expected_value)
                        )
                    )
                    for key, expected_value in expected_fields.items()
                ):
                    return StockConfigWriteResult(
                        ok=False,
                        changed=False,
                        field_keys=field_keys,
                        conflict_detected=True,
                        read_back_verified=False,
                        reason_code=STOCK_CONFIG_WRITE_FIELD_CONFLICT,
                        before_fingerprint=before_fingerprint,
                    )

                merged = deepcopy(current)
                for key, value in patch.items():
                    if value is STOCK_CONFIG_DELETE_FIELD:
                        merged.pop(key, None)
                    else:
                        merged[key] = deepcopy(value)
                if _json_values_equal(merged, current):
                    return StockConfigWriteResult(
                        ok=True,
                        changed=False,
                        field_keys=field_keys,
                        conflict_detected=False,
                        read_back_verified=True,
                        reason_code=STOCK_CONFIG_WRITE_NO_CHANGE,
                        before_fingerprint=before_fingerprint,
                        after_fingerprint=before_fingerprint,
                    )
                try:
                    json.dumps(merged, ensure_ascii=False)
                except (TypeError, ValueError):
                    return StockConfigWriteResult(
                        ok=False,
                        changed=False,
                        field_keys=field_keys,
                        conflict_detected=False,
                        read_back_verified=False,
                        reason_code=STOCK_CONFIG_WRITE_INVALID_PATCH,
                        before_fingerprint=before_fingerprint,
                    )

                try:
                    self._atomic_write_stock_config(
                        config_path,
                        merged,
                        expected_fingerprint=before_fingerprint,
                    )
                except _StockConfigConcurrentUpdateError:
                    continue
                except (OSError, TypeError, ValueError):
                    return StockConfigWriteResult(
                        ok=False,
                        changed=False,
                        field_keys=field_keys,
                        conflict_detected=False,
                        read_back_verified=False,
                        reason_code=STOCK_CONFIG_WRITE_ATOMIC_WRITE_FAILED,
                        before_fingerprint=before_fingerprint,
                    )

                read_back = self._read_stock_config_snapshot(config_path)
                if read_back.config is None or not _json_values_equal(
                    read_back.config, merged
                ):
                    return StockConfigWriteResult(
                        ok=False,
                        changed=True,
                        field_keys=field_keys,
                        conflict_detected=False,
                        read_back_verified=False,
                        reason_code=STOCK_CONFIG_WRITE_READBACK_FAILED,
                        before_fingerprint=before_fingerprint,
                        after_fingerprint=read_back.fingerprint,
                    )
                return StockConfigWriteResult(
                    ok=True,
                    changed=True,
                    field_keys=field_keys,
                    conflict_detected=False,
                    read_back_verified=True,
                    reason_code=STOCK_CONFIG_WRITE_OK,
                    before_fingerprint=before_fingerprint,
                    after_fingerprint=read_back.fingerprint,
                )

        return StockConfigWriteResult(
            ok=False,
            changed=False,
            field_keys=field_keys,
            conflict_detected=True,
            read_back_verified=False,
            reason_code=STOCK_CONFIG_WRITE_CONCURRENT_UPDATE_RETRY_EXHAUSTED,
            before_fingerprint=before_fingerprint,
        )

    def has_central_stocks(self) -> bool:
        if not self.stocks_dir.exists():
            return False
        return any(path.is_dir() for path in self.stocks_dir.iterdir())

    def list_stock_dirs(self) -> list[Path]:
        if not self.stocks_dir.exists():
            return []
        return sorted(
            [path for path in self.stocks_dir.iterdir() if path.is_dir()],
            key=lambda path: path.name,
        )

    def parse_stock_folder(self, path: Path) -> tuple[str, str]:
        name = path.name
        if "_" in name:
            code, stock_name = name.split("_", 1)
        else:
            code, stock_name = name, ""
        return normalize_stock_code(code), stock_name.strip()

    def load_config_routine(self, path: Path) -> str:
        """
        종목 config.json에서 현재 소속 루틴명을 읽는다.

        후보 필드:
        - routine
        - routine_name
        - assigned_routine
        - active_routine

        향후 실제 config 구조가 확정되면 하나로 고정한다.
        """
        config = read_json_dict(path / "config.json")
        for key in ("routine", "routine_name", "assigned_routine", "active_routine"):
            value = str(config.get(key, "")).strip()
            if value:
                return value
        return ""

    def load_config_assignment(self, path: Path) -> dict[str, str]:
        config = read_json_dict(path / "config.json")
        return {
            "assigned_routine_instance_id": str(
                config.get("assigned_routine_instance_id", "") or ""
            ).strip(),
            "routine_instance_name": str(
                config.get("routine_instance_name", "") or ""
            ).strip(),
            "routine_definition_id": str(
                config.get("routine_definition_id", "") or ""
            ).strip(),
            "routine_type": str(config.get("routine_type", "") or "").strip(),
        }

    @staticmethod
    def _assignment_history(config: dict[str, Any]) -> list[dict[str, Any]]:
        history = config.get(ROUTINE_ASSIGNMENT_HISTORY_KEY, [])
        if not isinstance(history, list):
            return []
        return [dict(item) for item in history if isinstance(item, dict)]

    @staticmethod
    def _close_assignment_history(
        config: dict[str, Any],
        *,
        instance_id: str,
        instance_name: str,
        definition_id: str,
        routine_type: str,
        changed_at: str,
    ) -> None:
        clean_instance_id = str(instance_id or "").strip()
        if not clean_instance_id:
            return
        history = StockRepository._assignment_history(config)
        open_item = next(
            (
                item
                for item in reversed(history)
                if str(item.get("instance_id", "") or "").strip() == clean_instance_id
                and not str(item.get("unregistered_at", "") or "").strip()
            ),
            None,
        )
        if open_item is None:
            open_item = {
                "instance_id": clean_instance_id,
                "instance_name": str(instance_name or "").strip(),
                "definition_id": str(definition_id or "").strip(),
                "routine_type": str(routine_type or "").strip(),
                "registered_at": "",
                "display_hidden": False,
            }
            history.append(open_item)
        open_item["unregistered_at"] = changed_at
        config[ROUTINE_ASSIGNMENT_HISTORY_KEY] = history

    @staticmethod
    def _open_assignment_history(
        config: dict[str, Any],
        *,
        instance_id: str,
        instance_name: str,
        definition_id: str,
        routine_type: str,
        changed_at: str,
    ) -> None:
        history = StockRepository._assignment_history(config)
        if any(
            str(item.get("instance_id", "") or "").strip() == instance_id
            and not str(item.get("unregistered_at", "") or "").strip()
            for item in history
        ):
            config[ROUTINE_ASSIGNMENT_HISTORY_KEY] = history
            return
        history.append(
            {
                "instance_id": instance_id,
                "instance_name": instance_name,
                "definition_id": definition_id,
                "routine_type": routine_type,
                "registered_at": changed_at,
                "unregistered_at": "",
                "display_hidden": False,
            }
        )
        config[ROUTINE_ASSIGNMENT_HISTORY_KEY] = history

    def list_routine_assignment_history(
        self,
        *,
        include_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        latest_by_instance_stock: dict[tuple[str, str], dict[str, Any]] = {}
        for path in self.list_stock_dirs():
            code, name = self.parse_stock_folder(path)
            config = read_json_dict(path / "config.json")
            for item in self._assignment_history(config):
                instance_id = str(item.get("instance_id", "") or "").strip()
                unregistered_at = str(item.get("unregistered_at", "") or "").strip()
                if not instance_id or not unregistered_at:
                    continue
                record = {
                    **item,
                    "instance_id": instance_id,
                    "stock_code": code,
                    "stock_name": name,
                    "stock_path": str(path.relative_to(self.project_root)),
                }
                key = (instance_id, code)
                previous = latest_by_instance_stock.get(key)
                if previous is None or str(previous.get("unregistered_at", "")) <= unregistered_at:
                    latest_by_instance_stock[key] = record
        records = [
            item
            for item in latest_by_instance_stock.values()
            if include_hidden or not bool(item.get("display_hidden", False))
        ]
        return sorted(
            records,
            key=lambda item: (
                str(item.get("instance_id", "")),
                str(item.get("stock_code", "")),
            ),
        )

    def hide_routine_assignment_history(
        self,
        *,
        code: str,
        instance_id: str,
    ) -> bool:
        path = self.resolve_stock_dir(code)
        if not path.exists():
            return False
        config_path = path / "config.json"
        config = read_json_dict(config_path)
        expected_history = (
            deepcopy(config[ROUTINE_ASSIGNMENT_HISTORY_KEY])
            if ROUTINE_ASSIGNMENT_HISTORY_KEY in config
            else STOCK_CONFIG_EXPECTED_MISSING
        )
        changed = False
        history = self._assignment_history(config)
        for item in history:
            if (
                str(item.get("instance_id", "") or "").strip() == str(instance_id or "").strip()
                and str(item.get("unregistered_at", "") or "").strip()
            ):
                item["display_hidden"] = True
                changed = True
        if not changed:
            return False
        result = self.patch_stock_config(
            code,
            {ROUTINE_ASSIGNMENT_HISTORY_KEY: history},
            expected_fields={ROUTINE_ASSIGNMENT_HISTORY_KEY: expected_history},
        )
        return result.ok and result.read_back_verified

    def list_from_central_stocks(self) -> list[StockRecord]:
        records: list[StockRecord] = []
        for path in self.list_stock_dirs():
            code, name = self.parse_stock_folder(path)
            if not is_valid_stock_code(code):
                continue
            routine = self.load_config_routine(path)
            assignment = self.load_config_assignment(path)
            records.append(
                StockRecord(
                    code=code,
                    name=name,
                    routine=routine,
                    enabled=True,
                    stock_path=str(path.relative_to(self.project_root)),
                    **assignment,
                )
            )
        return records

    def list_stocks(self) -> list[StockRecord]:
        return self.list_from_central_stocks()

    def list_current_registered_stocks(self) -> list[StockRecord]:
        """Return Stocks whose current assignment names a persisted Instance."""

        valid_instance_ids = {
            str(instance.instance_id or "").strip()
            for instance in load_persisted_routine_instances(project_root=self.project_root)
            if str(instance.instance_id or "").strip()
        }
        return [
            record
            for record in self.list_stocks()
            if str(record.assigned_routine_instance_id or "").strip()
            in valid_instance_ids
        ]

    def realtime_monitoring_universe(self) -> RealtimeMonitoringUniverseProjection:
        """Project current registered Stocks into a Broker-safe read-only target set."""

        records = self.list_current_registered_stocks()
        valid_codes = {
            normalize_stock_code(record.code)
            for record in records
            if is_valid_stock_code(record.code)
        }
        unsupported = tuple(
            sorted(code for code in valid_codes if not is_broker_action_stock_code(code))
        )
        return RealtimeMonitoringUniverseProjection(
            target_stock_codes=tuple(
                sorted(code for code in valid_codes if code not in unsupported)
            ),
            unsupported_stock_codes=unsupported,
            source_record_count=len(records),
        )

    def read_base_stocks_compatible(self) -> list[dict[str, Any]]:
        return [record.to_base_stock_dict() for record in self.list_stocks()]

    def find_by_code(self, code: str) -> StockRecord | None:
        target_code = normalize_stock_code(code)
        for record in self.list_stocks():
            if record.code == target_code:
                return record
        return None

    def resolve_stock_dir(self, code: str, name: str = "") -> Path:
        record = self.find_by_code(code)
        if record and record.stock_path:
            return self.project_root / record.stock_path
        return self.stocks_dir / safe_stock_folder_name(code, name)

    def ensure_stock_folder(self, code: str, name: str, routine: str = "") -> Path:
        """
        중앙 stocks/ 종목 폴더를 생성한다.

        주의:
        - 기존 루틴폴더를 건드리지 않는다.
        - state/config/orders 기본 파일만 없을 때 생성한다.
        """
        clean_code = normalize_stock_code(code)
        if not is_valid_stock_code(clean_code):
            raise ValueError("stock code is invalid")
        path = self.resolve_stock_dir(clean_code, name)
        path.mkdir(parents=True, exist_ok=True)
        (path / "logs").mkdir(exist_ok=True)

        state_path = path / "state.json"
        config_path = path / "config.json"
        orders_path = path / "orders.json"

        if not state_path.exists():
            write_json_dict(
                state_path,
                {
                    "status": "STOPPED",
                    "holding_qty": 0,
                    "avg_price": 0,
                    "created_at": now_text(),
                    "updated_at": now_text(),
                },
            )

        if not config_path.exists():
            write_json_dict(
                config_path,
                {
                    "routine": routine,
                    "enabled": True,
                    "created_at": now_text(),
                    "updated_at": now_text(),
                },
            )

        if not orders_path.exists():
            write_json_dict(
                orders_path,
                {
                    "orders": [],
                    "updated_at": now_text(),
                },
            )

        return path


def repository() -> StockRepository:
    return StockRepository()


def read_base_stocks_from_repository() -> list[dict[str, Any]]:
    return repository().read_base_stocks_compatible()



def stock_runtime_dir_from_repository(code: str, name: str = "") -> Path:
    return repository().resolve_stock_dir(code, name)
