# -*- coding: utf-8 -*-
"""
gui_base_stock_service.py

중앙 stocks/ 종목관리 및 stock_library.json 관련 순수 서비스 헬퍼.
- 종목 라이브러리 로딩
- 중앙 stocks/ 종목 목록 읽기/갱신
- 종목당 단일 활성 루틴 보정

주의:
- GUI 위젯 직접 조작 없음.
- 자동매매 마감/청산/현황 정책 없음.
"""

from __future__ import annotations

import json
from pathlib import Path

from gui_routine_service import ensure_single_real_trade_routine_for_stock
from gui_stock_data import load_stock_library as load_verified_runtime_stock_library
from stock_code_contract import (
    is_valid_stock_code as canonical_is_valid_stock_code,
    normalize_stock_code as canonical_normalize_stock_code,
)

try:
    from assignment_episode_linkage import assign_stock_routine, unassign_stock_routine
    from stock_repository import repository as stock_repository_factory
except Exception:
    assign_stock_routine = None
    stock_repository_factory = None
    unassign_stock_routine = None


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_STOCK_PATH = PROJECT_ROOT / "기초종목.txt"
STOCK_LIBRARY_PATH = PROJECT_ROOT / "stock_library.json"


def normalize_stock_code(code: str) -> str:
    """
    종목코드는 자동 보정하지 않고 앞뒤 공백만 제거한다.

    주의:
    - 930 -> 000930 같은 zfill 보정은 금지한다.
    - 사용자가 입력한 값이 canonical 6자리 영숫자여야 한다.
    """
    return canonical_normalize_stock_code(code)


def is_valid_stock_code(code: str) -> bool:
    """
    종목코드 기본 형식 검증.
    """
    return canonical_is_valid_stock_code(code)


def load_stock_library() -> list[dict[str, str]]:
    """
    stock_library.json 을 읽어 검색 등록용 종목 목록으로 변환한다.

    로그인 세션에서 검증 완료된 runtime Master Library만 사용한다.
    """
    return load_verified_runtime_stock_library()


def find_library_stock_by_code(code: str) -> dict[str, str] | None:
    """
    종목코드 기준으로 stock_library.json 의 종목을 찾는다.
    """
    normalized_code = normalize_stock_code(code)
    for stock in load_stock_library():
        if stock.get("code", "") == normalized_code:
            return stock
    return None


def validate_base_stock_record(
    code: str,
    name: str,
    line_no: int,
    seen_codes: set[str],
    seen_names: set[str],
) -> str:
    """
    기초종목.txt 에 이미 저장된 종목 1행의 표시용 검증 상태를 반환한다.

    중요:
    - 기존에 잘못 저장된 000000, 임의 종목명, 라이브러리 불일치 데이터를 정상으로 표시하지 않는다.
    - 등록 가능 여부는 stock_library.json 을 기준으로 판단한다.
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


def single_routine_list(routines: list[str]) -> list[str]:
    """
    기초종목.txt 활성 루틴은 종목당 1개만 허용한다.

    루틴 폴더에 과거 종목 폴더가 남아 있어도,
    활성 연결은 기초종목.txt의 첫 번째 유효 루틴 1개만 사용한다.
    """
    clean: list[str] = []
    seen: set[str] = set()
    for routine in routines:
        routine_name = str(routine).strip()
        if routine_name and routine_name not in seen:
            clean.append(routine_name)
            seen.add(routine_name)
    return clean[:1]


def normalize_base_stock_single_routine_file() -> bool:
    """
    과거 기초종목.txt 단일 루틴 보정 함수.

    중앙 stocks/ 구조 도입 이후 운영 기준으로는 사용하지 않는다.
    호환을 위해 함수명만 유지하고 아무 작업도 하지 않는다.
    """
    return False



def _central_repository_available() -> bool:
    """
    중앙 stocks/ 구조 사용 가능 여부.
    1차 안전장치: stocks/ 종목폴더가 없으면 기존 기초종목.txt 로직을 유지한다.
    """
    if stock_repository_factory is None:
        return False
    try:
        return bool(stock_repository_factory().has_central_stocks())
    except Exception:
        return False


def _read_base_stocks_from_central_repository_if_available() -> list[dict[str, object]] | None:
    """
    중앙 stocks/ 종목관리 구조가 준비된 경우에만 repository 기준으로 종목 목록을 읽는다.
    """
    if not _central_repository_available():
        return None
    try:
        return stock_repository_factory().read_base_stocks_compatible()
    except Exception:
        return None


def _update_base_stock_routines_in_central_repository_if_available(
    code: str,
    name: str,
    routines: list[str],
    *,
    expected_instance_id: str | None = None,
) -> bool | None:
    """
    중앙 stocks/ 종목관리 구조가 준비된 경우에만 config.json의 routine 값을 갱신한다.

    반환:
    - True/False: 중앙 repository로 처리함
    - None: 중앙 repository 미사용, 기존 기초종목.txt fallback 필요
    """
    if not _central_repository_available() or unassign_stock_routine is None:
        return None
    try:
        repository = stock_repository_factory()
        return bool(
            unassign_stock_routine(
                repository.project_root,
                code,
                name,
                routines,
                expected_instance_id=expected_instance_id,
                stock_repository=repository,
            ).ok
        )
    except Exception:
        return None


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



def ensure_single_real_trade_routine_for_all_stocks() -> None:
    """
    기존 데이터 마이그레이션용 보정.
    기초종목.txt에 연결된 각 종목마다 실주문 루틴이 최대 1개가 되도록 정리한다.
    """
    for stock in read_base_stocks():
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "")).strip()
        if code and name:
            ensure_single_real_trade_routine_for_stock(code, name)


def update_base_stock_routines(
    code: str,
    name: str,
    routines: list[str],
    *,
    expected_instance_id: str | None = None,
) -> bool:
    """
    특정 종목의 활성 루틴 목록을 중앙 stocks/종목/config.json에 반영한다.

    최종 중앙화 원칙:
    - 기초종목.txt를 갱신하지 않는다.
    - 중앙 stocks/ 구조가 없으면 실패(False)로 처리한다.
    """
    central_updated = _update_base_stock_routines_in_central_repository_if_available(
        code,
        name,
        routines,
        expected_instance_id=expected_instance_id,
    )
    if central_updated is not None:
        return central_updated
    return False


def update_base_stock_routines_result(
    code: str,
    name: str,
    routines: list[str],
    *,
    expected_instance_id: str | None = None,
):
    """Return the repository's semantic Assignment result for guarded callers."""
    if not _central_repository_available() or unassign_stock_routine is None:
        return None
    try:
        repository = stock_repository_factory()
        return unassign_stock_routine(
            repository.project_root,
            code,
            name,
            routines,
            expected_instance_id=expected_instance_id,
            stock_repository=repository,
        )
    except Exception:
        return None


def update_base_stock_routine_instance(
    code: str,
    name: str,
    *,
    instance_id: str,
    instance_name: str,
    definition_id: str,
    routine_type: str,
    expected_instance_id: str | None = None,
) -> bool:
    if not _central_repository_available() or assign_stock_routine is None:
        return False
    try:
        repository = stock_repository_factory()
        return bool(
            assign_stock_routine(
                repository.project_root,
                code,
                name,
                instance_id=instance_id,
                instance_name=instance_name,
                definition_id=definition_id,
                routine_type=routine_type,
                expected_instance_id=expected_instance_id,
                stock_repository=repository,
            ).ok
        )
    except Exception:
        return False
