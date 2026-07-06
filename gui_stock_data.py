# -*- coding: utf-8 -*-
"""
gui_stock_data.py

기초종목/종목 데이터 조회용 Repository 함수 모음.

현재 단계:
- gui_windows.py에서 안전하게 분리 가능한 읽기 전용 함수부터 이동한다.
- UI, QMessageBox, QTableWidget에 의존하지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from gui_common_utils import sanitize_path_part

try:
    from stock_repository import repository as stock_repository_factory
except Exception:
    stock_repository_factory = None


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_STOCK_PATH = PROJECT_ROOT / "기초종목.txt"
STOCK_LIBRARY_PATH = PROJECT_ROOT / "stock_library.json"


def normalize_stock_code(code: str) -> str:
    """
    종목코드는 자동 보정하지 않고 앞뒤 공백만 제거한다.
    """
    return str(code).strip()


def is_valid_stock_code(code: str) -> bool:
    """
    종목코드 기본 형식 검증.
    """
    code_text = str(code).strip()
    return code_text.isdigit() and len(code_text) == 6 and code_text != "000000"


def load_stock_library() -> list[dict[str, str]]:
    """
    stock_library.json 을 읽어 검색 등록용 종목 목록으로 변환한다.
    """
    if not STOCK_LIBRARY_PATH.exists():
        return []

    try:
        data = json.loads(STOCK_LIBRARY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    stocks: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        market = str(item.get("market", "")).strip()
        chosung = str(item.get("chosung", "")).strip()

        if not code or not name:
            continue

        stocks.append(
            {
                "code": code,
                "name": name,
                "market": market,
                "chosung": chosung,
            }
        )

    return stocks



def find_library_stock_by_code(code: str) -> dict[str, str] | None:
    """
    종목코드 기준으로 stock_library.json 의 종목을 찾는다.
    """
    normalized_code = normalize_stock_code(code)
    for stock in load_stock_library():
        if stock.get("code", "") == normalized_code:
            return stock
    return None


def single_routine_list(routines: list[str]) -> list[str]:
    """
    기초종목.txt 활성 루틴은 종목당 1개만 허용한다.
    """
    clean: list[str] = []
    seen: set[str] = set()
    for routine in routines:
        routine_name = str(routine).strip()
        if routine_name and routine_name not in seen:
            clean.append(routine_name)
            seen.add(routine_name)
    return clean[:1]


def validate_base_stock_record(
    code: str,
    name: str,
    line_no: int,
    seen_codes: set[str],
    seen_names: set[str],
) -> str:
    """
    기초종목.txt 에 저장된 종목 1행의 표시용 검증 상태를 반환한다.
    """
    errors: list[str] = []

    if not is_valid_stock_code(code):
        errors.append("종목코드 오류")

    if not name:
        errors.append("종목명 오류")

    if code in seen_codes:
        errors.append("중복 코드")

    if name and name in seen_names:
        errors.append("중복 종목명")

    library_stock = find_library_stock_by_code(code)
    if library_stock is None:
        errors.append("라이브러리 없음")
    else:
        library_name = library_stock.get("name", "").strip()
        if name != library_name:
            errors.append("라이브러리 불일치")

    if errors:
        return f"{line_no}행: " + ", ".join(errors)

    return "정상"



def _read_base_stocks_from_central_repository_if_available() -> list[dict[str, object]] | None:
    """
    중앙 stocks/ 종목관리 구조가 준비된 경우에만 repository 기준으로 종목 목록을 읽는다.

    1차 개편 안전장치:
    - 아직 stocks/ 중앙 종목폴더가 없으면 None을 반환한다.
    - 그러면 기존 기초종목.txt 로직이 그대로 동작한다.
    - repository 로딩/읽기 중 오류가 나도 기존 로직으로 fallback한다.
    """
    if stock_repository_factory is None:
        return None

    try:
        repo = stock_repository_factory()
        if not repo.has_central_stocks():
            return None
        return repo.read_base_stocks_compatible()
    except Exception:
        return None


def _central_repository_if_available():
    """
    중앙 stocks/ 구조가 실제로 존재할 때만 repository 인스턴스를 반환한다.
    아직 중앙 구조가 없거나 오류가 있으면 None을 반환하여 기존 로직을 fallback으로 사용한다.
    """
    if stock_repository_factory is None:
        return None
    try:
        repo = stock_repository_factory()
        if not repo.has_central_stocks():
            return None
        return repo
    except Exception:
        return None


def _base_stock_routines_from_central_repository_if_available(
    code: str,
    name: str,
) -> tuple[bool, list[str]] | None:
    """
    중앙 stocks/ 기준으로 종목 존재 여부와 활성 루틴을 반환한다.

    반환:
    - None: 중앙 repository 미사용, 기존 기초종목.txt fallback 필요
    - (False, []): 중앙 stocks/ 구조는 있으나 해당 종목 없음
    - (True, []): 종목은 있으나 현재 루틴 없음
    - (True, [routine]): 종목의 현재 활성 루틴
    """
    repo = _central_repository_if_available()
    if repo is None:
        return None

    record = repo.find_by_code(code)
    if record is None:
        return False, []

    routine = str(record.routine or "").strip()
    return True, ([routine] if routine else [])




def append_base_stock(code: str, name: str) -> bool:
    """
    종목을 중앙 stocks/ 구조에 등록한다.

    기존 호환 함수명 유지:
    - 과거에는 기초종목.txt에 종목을 추가했다.
    - 현재는 중앙 stocks/종목폴더를 생성한다.
    - stock_library.json은 검색용 라이브러리로만 유지한다.
    """
    clean_code = normalize_stock_code(str(code))
    clean_name = str(name or "").strip()

    if not is_valid_stock_code(clean_code) or not clean_name:
        return False

    if stock_repository_factory is None:
        return False

    try:
        repo = stock_repository_factory()
        repo.ensure_stock_folder(clean_code, clean_name, routine="")
        return True
    except Exception:
        return False


def remove_base_stock(code: str, name: str) -> bool:
    """
    중앙 종목관리 기준 종목 등록해제 후보 함수.

    주의:
    - 현재 정책상 종목 폴더 삭제는 별도 확정 전이므로 실제 삭제하지 않는다.
    - 완전 삭제 대신 루틴 연결만 비우는 안전 동작으로 둔다.
    """
    clean_code = normalize_stock_code(str(code))
    clean_name = str(name or "").strip()

    if not is_valid_stock_code(clean_code) or not clean_name:
        return False

    if stock_repository_factory is None:
        return False

    try:
        repo = stock_repository_factory()
        if repo.find_by_code(clean_code) is None:
            return False
        return bool(repo.update_stock_routine(clean_code, clean_name, []))
    except Exception:
        return False


def read_base_stocks() -> list[dict[str, object]]:
    """
    중앙 stocks/ 종목 목록을 읽는다.

    최종 중앙화 원칙:
    - 기초종목.txt를 운영 기준으로 사용하지 않는다.
    - 중앙 stocks/ 구조가 없거나 repository 로딩에 실패하면 빈 목록을 반환한다.
    """
    central_stocks = _read_base_stocks_from_central_repository_if_available()
    if central_stocks is not None:
        return central_stocks
    return []



def _first_routine_from_line_parts(parts: list[str]) -> str:
    """기초종목.txt 한 줄에서 첫 번째 활성 루틴명을 반환한다."""
    seen: set[str] = set()
    for raw in parts[2:]:
        routine_name = str(raw).strip()
        if routine_name and routine_name not in seen:
            return routine_name
        if routine_name:
            seen.add(routine_name)
    return ""


def base_stock_routines_for_stock(code: str, name: str) -> tuple[bool, list[str]]:
    """
    중앙 stocks/ 기준으로 대상 종목 존재 여부와 등록 루틴 목록을 반환한다.

    반환값:
    - (False, []) : 대상 종목 없음 또는 중앙 구조 미준비
    - (True, [])  : 대상 종목은 있으나 등록 루틴 없음
    - (True, [...]) : 대상 종목의 등록 루틴 목록
    """
    central_result = _base_stock_routines_from_central_repository_if_available(
        str(code).strip(),
        str(name).strip(),
    )
    if central_result is not None:
        return central_result
    return False, []



def active_routine_for_stock(code: str, name: str) -> str:
    """
    중앙 stocks/ 기준 현재 활성 루틴명 1개를 반환한다.
    """
    exists, routines = base_stock_routines_for_stock(code, name)
    if not exists or not routines:
        return ""
    return str(routines[0]).strip()



def stock_runtime_dir_for_routine(routine_name: str, code: str, name: str) -> Path | None:
    """
    표시 루틴명, 종목코드, 종목명 기준으로 중앙 stocks/ 종목 폴더 경로를 찾는다.

    최종 중앙화 원칙:
    - 기존 _루틴명/종목 폴더를 fallback으로 사용하지 않는다.
    - 요청 루틴명과 config의 현재 루틴명이 다르면 None을 반환한다.
    """
    requested_routine = str(routine_name or "").strip()

    repo = _central_repository_if_available()
    if repo is None:
        return None

    record = repo.find_by_code(code)
    if record is None:
        return None

    current_routine = str(record.routine or "").strip()
    if requested_routine and current_routine != requested_routine:
        return None

    stock_dir = repo.resolve_stock_dir(code, name)
    if stock_dir.exists() and stock_dir.is_dir():
        return stock_dir
    return None



def assigned_runtime_dirs_for_stock(code: str, name: str) -> list[tuple[str, Path]]:
    """
    해당 종목이 배정된 루틴 runtime 폴더를 반환한다.

    중앙 stocks/ 구조에서는 종목당 현재 활성 루틴 1개와 중앙 stocks/종목 폴더만 반환한다.
    중앙 구조가 없을 때만 기존 기초종목.txt + 루틴폴더 방식을 fallback으로 사용한다.
    """
    found, routines = base_stock_routines_for_stock(code, name)
    if not found:
        return []

    result: list[tuple[str, Path]] = []
    for routine_name in routines:
        stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
        if stock_dir is not None and stock_dir.exists() and stock_dir.is_dir():
            result.append((routine_name, stock_dir))

    return result



def write_stock_config(stock_dir: Path, config: dict[str, object]) -> None:
    """
    종목 runtime 폴더의 config.json을 저장한다.

    Repository 저장 함수이다.
    """
    stock_dir.mkdir(parents=True, exist_ok=True)
    (stock_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
