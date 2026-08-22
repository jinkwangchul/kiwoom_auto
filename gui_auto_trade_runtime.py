# -*- coding: utf-8 -*-
"""
gui_auto_trade_runtime.py

자동매매 런타임 공통 헬퍼.
- 시간 문자열
- 종목 폴더명 파싱
- 루틴 내 종목 폴더 조회
- state.json 안전 저장

주의:
- GUI 위젯 조작은 포함하지 않는다.
- 정책 판정은 gui_auto_trade_policy.py에 둔다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from gui_auto_trade_integrity import (
    is_review_required_state,
    read_review_state_with_issue,
)
from group_scope import load_group_scope
from gui_routine_registry import scan_group_records


PROJECT_ROOT = Path(__file__).resolve().parent
CENTRAL_STOCKS_DIR = PROJECT_ROOT / "stocks"


def now_text() -> str:
    """공통 업데이트 시각 문자열."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_stock_folder_name(folder_name: str) -> tuple[str, str]:
    """종목 폴더명에서 코드/종목명을 분리한다.

    예: 005930_삼성전자 -> ("005930", "삼성전자")
    """
    parts = str(folder_name).split("_", 1)
    if len(parts) != 2:
        return "", str(folder_name).strip()
    return parts[0].strip(), parts[1].strip()


def _central_stocks_available() -> bool:
    """중앙 stocks/ 종목폴더 사용 가능 여부."""
    if not CENTRAL_STOCKS_DIR.exists() or not CENTRAL_STOCKS_DIR.is_dir():
        return False
    try:
        return any(child.is_dir() for child in CENTRAL_STOCKS_DIR.iterdir())
    except Exception:
        return False


def all_registered_stock_dirs() -> list[Path]:
    """Return every registered stock directory from the central store."""
    if not _central_stocks_available():
        return []
    try:
        result = [
            stock_dir
            for stock_dir in CENTRAL_STOCKS_DIR.iterdir()
            if (
                stock_dir.is_dir()
                and not stock_dir.name.startswith(".")
                and not stock_dir.name.startswith("__")
                and (stock_dir / "config.json").exists()
                and (stock_dir / "state.json").exists()
            )
        ]
    except Exception:
        return []
    result.sort(key=lambda path: path.name)
    return result


def stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """Compatibility adapter from a physical legacy Group path to logical scope."""
    target = Path(routine_dir).resolve(strict=False)
    group = next(
        (
            record
            for record in scan_group_records()
            if record.path.resolve(strict=False) == target
        ),
        None,
    )
    if group is None:
        return []
    return list(load_group_scope().group_stock_dirs(group.group_id))


def group_stock_dirs(group_id: str) -> list[Path]:
    return list(load_group_scope().group_stock_dirs(group_id))


def instance_stock_dirs(instance_id: str) -> list[Path]:
    return list(load_group_scope().instance_stock_dirs(instance_id))


def all_group_stock_dirs() -> list[Path]:
    return list(load_group_scope().all_group_stock_dirs())


def get_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """기존 호출명 호환용 alias."""
    return stock_dirs_in_routine(routine_dir)


def assigned_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """자동매매설정 하단 종목표 기존 호출명 호환용 alias."""
    return stock_dirs_in_routine(routine_dir)


def write_json_file(path: Path, data: dict[str, object]) -> bool:
    """dict를 JSON 파일로 저장한다. 실패 시 False."""
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def write_state_json(
    stock_dir: Path,
    state: dict[str, object],
    *,
    allow_review_state_transition: bool = False,
) -> bool:
    """종목 state.json 저장 공통 함수."""
    if not allow_review_state_transition:
        current_state, state_issue_reason = read_review_state_with_issue(stock_dir / "state.json")
        next_status = str(state.get("status", "") or "").strip().upper()
        if (
            (state_issue_reason or is_review_required_state(current_state))
            and next_status not in {"REVIEW_REQUIRED", "REVIEW"}
        ):
            return False
    return write_json_file(stock_dir / "state.json", state)
