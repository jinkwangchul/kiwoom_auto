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

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
        config[ROUTINE_ASSIGNMENT_HISTORY_KEY] = history
        write_json_dict(config_path, config)
        return True

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

    def update_stock_routine(
        self,
        code: str,
        name: str,
        routines: list[str],
        *,
        _episode_before_target=None,
    ) -> bool:
        """
        중앙 stocks/ 구조에서 종목의 현재 소속 루틴을 config.json에 반영한다.

        정책:
        - 종목당 활성 루틴은 1개만 사용한다.
        - holding_qty 등 현재 상태값은 절대 수정하지 않는다.
        - state.json은 종목의 진실이므로 이 함수에서 건드리지 않는다.
        """
        clean_routines: list[str] = []
        seen: set[str] = set()
        for routine in routines:
            routine_name = str(routine or "").strip()
            if routine_name and routine_name not in seen:
                clean_routines.append(routine_name)
                seen.add(routine_name)
        routine_name = clean_routines[0] if clean_routines else ""

        path = self.resolve_stock_dir(code, name)
        if not path.exists():
            return False

        config_path = path / "config.json"
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        before_config = deepcopy(config)
        before_assignment = _routine_assignment(config)
        changed_at = now_text()
        self._close_assignment_history(
            config,
            instance_id=str(config.get("assigned_routine_instance_id", "") or ""),
            instance_name=str(config.get("routine_instance_name", "") or ""),
            definition_id=str(config.get("routine_definition_id", "") or ""),
            routine_type=str(config.get("routine_type", "") or ""),
            changed_at=changed_at,
        )

        # 루틴 연결 정보 일원화
        config["routine"] = routine_name
        config["routine_name"] = routine_name

        # 과거 구조 호환 필드도 함께 갱신/정리한다.
        # 읽기 쪽에서 여러 후보 필드를 검사하므로 해제 시 전부 비워야 한다.
        config["assigned_routine"] = routine_name
        config["active_routine"] = routine_name
        config["routines"] = [routine_name] if routine_name else []
        config["assigned_routine_instance_id"] = ""
        config["routine_instance_name"] = ""
        config["routine_definition_id"] = ""
        config["routine_type"] = routine_name

        config["updated_at"] = changed_at
        from assignment_episode_linkage import commit_assignment_with_episode

        linkage = commit_assignment_with_episode(
            self.project_root,
            normalize_stock_code(code),
            config_path,
            before_config,
            config,
            changed_at=datetime.now().astimezone(),
            reason="STOCK_ASSIGNMENT_REMOVED",
            source="STOCK_REPOSITORY",
            before_target=_episode_before_target,
        )
        self.last_assignment_linkage_result = linkage
        if not linkage.success:
            return False
        saved = read_json_dict(config_path)
        expected_assignment = _routine_assignment(config)
        saved_assignment = _routine_assignment(saved)
        if saved_assignment != expected_assignment:
            return False
        _append_routine_changed(
            code=code,
            name=name,
            before=before_assignment,
            after=saved_assignment,
        )
        return True

    def update_stock_routine_instance(
        self,
        code: str,
        name: str,
        *,
        instance_id: str,
        instance_name: str,
        definition_id: str,
        routine_type: str,
    ) -> bool:
        clean_instance_id = str(instance_id or "").strip()
        clean_instance_name = str(instance_name or "").strip()
        clean_definition_id = str(definition_id or "").strip()
        clean_routine_type = str(routine_type or "").strip()
        if not all(
            (
                clean_instance_id,
                clean_instance_name,
                clean_definition_id,
                clean_routine_type,
            )
        ):
            return False

        path = self.resolve_stock_dir(code, name)
        if not path.exists():
            return False
        config_path = path / "config.json"
        config = read_json_dict(config_path)
        current_instance_id = str(
            config.get("assigned_routine_instance_id", "") or ""
        ).strip()
        if current_instance_id == clean_instance_id:
            return True
        before_config = deepcopy(config)
        before_assignment = _routine_assignment(config)
        changed_at = now_text()
        previous_instance_id = str(
            config.get("assigned_routine_instance_id", "") or ""
        ).strip()
        if previous_instance_id and previous_instance_id != clean_instance_id:
            self._close_assignment_history(
                config,
                instance_id=previous_instance_id,
                instance_name=str(config.get("routine_instance_name", "") or ""),
                definition_id=str(config.get("routine_definition_id", "") or ""),
                routine_type=str(config.get("routine_type", "") or ""),
                changed_at=changed_at,
            )
        self._open_assignment_history(
            config,
            instance_id=clean_instance_id,
            instance_name=clean_instance_name,
            definition_id=clean_definition_id,
            routine_type=clean_routine_type,
            changed_at=changed_at,
        )
        config["routine"] = clean_routine_type
        config["routine_name"] = clean_routine_type
        config["assigned_routine"] = clean_routine_type
        config["active_routine"] = clean_routine_type
        config["routines"] = [clean_routine_type]
        config["assigned_routine_instance_id"] = clean_instance_id
        config["routine_instance_name"] = clean_instance_name
        config["routine_definition_id"] = clean_definition_id
        config["routine_type"] = clean_routine_type
        config["updated_at"] = changed_at
        from assignment_episode_linkage import commit_assignment_with_episode

        linkage = commit_assignment_with_episode(
            self.project_root,
            normalize_stock_code(code),
            config_path,
            before_config,
            config,
            changed_at=datetime.now().astimezone(),
            reason="STOCK_ASSIGNMENT_CHANGED",
            source="STOCK_REPOSITORY",
        )
        self.last_assignment_linkage_result = linkage
        if not linkage.success:
            return False
        saved = read_json_dict(config_path)
        expected_assignment = _routine_assignment(config)
        saved_assignment = _routine_assignment(saved)
        if saved_assignment != expected_assignment:
            return False
        _append_routine_changed(
            code=code,
            name=name,
            before=before_assignment,
            after=saved_assignment,
        )
        return True

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



def update_base_stock_routines_in_repository(code: str, name: str, routines: list[str]) -> bool:
    """
    기존 update_base_stock_routines() 교체 후보 함수.
    중앙 stocks/ 구조에서는 config.json의 routine 값을 갱신한다.
    """
    return repository().update_stock_routine(code, name, routines)

def stock_runtime_dir_from_repository(code: str, name: str = "") -> Path:
    return repository().resolve_stock_dir(code, name)
