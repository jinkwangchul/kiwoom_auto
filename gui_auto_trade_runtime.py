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

from gui_routine_registry import routine_display_name as registry_routine_display_name


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


def _read_json_dict(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _central_stocks_available() -> bool:
    """중앙 stocks/ 종목폴더 사용 가능 여부."""
    if not CENTRAL_STOCKS_DIR.exists() or not CENTRAL_STOCKS_DIR.is_dir():
        return False
    try:
        return any(child.is_dir() for child in CENTRAL_STOCKS_DIR.iterdir())
    except Exception:
        return False


def _routine_display_name_from_dir(routine_dir: Path) -> str:
    """루틴 원본 경로에서 화면 표시 루틴명을 만든다."""
    if routine_dir is None:
        return ""
    return registry_routine_display_name(routine_dir).strip()


def _routine_values_from_config(config: dict[str, Any]) -> list[str]:
    """종목 config.json 안의 루틴 관련 값을 모두 수집한다."""
    values: list[str] = []

    for key in ("routine", "routine_name", "assigned_routine", "active_routine"):
        value = config.get(key, "")
        if isinstance(value, str):
            text = value.strip()
            if text:
                values.append(text)
        elif isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text:
                    values.append(text)

    routines = config.get("routines")
    if isinstance(routines, list):
        for item in routines:
            text = str(item or "").strip()
            if text:
                values.append(text)

    # 중복 제거, 순서 유지
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _central_stock_dirs_for_routine(routine_name: str) -> list[Path]:
    """
    중앙 stocks/ 기준으로 특정 루틴에 연결된 종목폴더를 조회한다.

    정책:
    - stocks/가 존재하면 이것을 현재 종목 진실의 기준으로 본다.
    - 기존 _루틴폴더 직접 스캔은 stocks/가 없을 때만 fallback한다.
    """
    target_routine = str(routine_name or "").strip()
    if not target_routine or not _central_stocks_available():
        return []

    result: list[Path] = []
    try:
        for stock_dir in CENTRAL_STOCKS_DIR.iterdir():
            if not stock_dir.is_dir():
                continue
            if stock_dir.name.startswith(".") or stock_dir.name.startswith("__"):
                continue
            config = _read_json_dict(stock_dir / "config.json")
            routine_values = _routine_values_from_config(config)
            if target_routine in routine_values:
                result.append(stock_dir)
    except Exception:
        return []

    result.sort(key=lambda path: path.name)
    return result


def _legacy_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """기존 루틴폴더 아래 종목 runtime 폴더 직접 조회."""
    if routine_dir is None or not routine_dir.exists() or not routine_dir.is_dir():
        return []
    result = [
        child
        for child in routine_dir.iterdir()
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and not child.name.startswith("__")
        )
    ]
    result.sort(key=lambda path: path.name)
    return result


def stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """
    루틴에 연결된 종목 runtime 폴더를 조회한다.

    중앙화 개편 이후 기준:
    - stocks/ 중앙 종목폴더가 존재하면 stocks/*/config.json의 routine 값을 기준으로 조회한다.
    - stocks/가 아직 없을 때만 기존 루틴폴더 직접 스캔을 사용한다.

    이렇게 해야 자동매매설정 하단 종목표와 해제 가능/불가 판정이
    과거 구형 루틴폴더 잔재가 아니라 중앙 종목 상태를 기준으로 동작한다.
    """
    routine_name = _routine_display_name_from_dir(routine_dir)
    central_dirs = _central_stock_dirs_for_routine(routine_name)
    if _central_stocks_available():
        return central_dirs
    return _legacy_stock_dirs_in_routine(routine_dir)


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


def write_state_json(stock_dir: Path, state: dict[str, object]) -> bool:
    """종목 state.json 저장 공통 함수."""
    return write_json_file(stock_dir / "state.json", state)
