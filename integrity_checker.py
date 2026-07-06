# -*- coding: utf-8 -*-

"""
integrity_checker.py

키움 OpenAPI 자동매매 시스템 무결성검증 로직.

2026-06-16 갱신:
- 루틴 원본을 구형 _루틴폴더/budget.json 기준이 아니라 routines/<루틴명>/routine.json 기준으로 검사한다.
- 종목 원본을 루틴폴더 내부 종목폴더가 아니라 중앙 stocks/<종목코드_종목명>/ 기준으로 검사한다.
- 삭제/격리는 수행하지 않는다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from gui_routine_registry import (
        get_routine_records,
        routine_names,
        normalize_routine_name as registry_normalize_routine_name,
    )
except Exception:  # 단독 검사/초기 구동 보호
    get_routine_records = None  # type: ignore[assignment]
    routine_names = None  # type: ignore[assignment]
    registry_normalize_routine_name = None  # type: ignore[assignment]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def decode_hash_unicode(text: object) -> str:
    """폴더명에 남은 #UXXXX 표기를 사람이 읽는 문자로 복원한다."""
    value = str(text or "")

    def repl(match: re.Match[str]) -> str:
        try:
            return chr(int(match.group(1), 16))
        except Exception:
            return match.group(0)

    return re.sub(r"#U([0-9A-Fa-f]{4})", repl, value)


LEGACY_ROUTINE_ALIASES: dict[str, str] = {
    "등록확인폴더": "등록확인루틴",
    "_등록확인폴더": "등록확인루틴",
    "지표추종매매": "지표추종매매",
    "_지표추종매매": "지표추종매매",
}


def normalize_routine_name(value: object) -> str:
    """구형 루틴명과 신규 루틴 패키지명을 같은 기준으로 맞춘다."""
    if registry_normalize_routine_name is not None:
        try:
            return str(registry_normalize_routine_name(value)).strip()
        except Exception:
            pass

    text = decode_hash_unicode(value).strip()
    if text in LEGACY_ROUTINE_ALIASES:
        return LEGACY_ROUTINE_ALIASES[text]

    if text.startswith("_"):
        text = text.lstrip("_").strip()

    return LEGACY_ROUTINE_ALIASES.get(text, text)


def is_valid_stock_code(code: str) -> bool:
    return code.isdigit() and len(code) == 6 and code != "000000"


def load_stock_library(stock_library_path: Path) -> list[dict[str, str]]:
    if not stock_library_path.exists():
        return []

    try:
        data = json.loads(stock_library_path.read_text(encoding="utf-8"))
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


def read_base_stocks_for_check(base_stock_path: Path) -> list[dict[str, object]]:
    """
    구형 기초종목.txt 검사 호환용.
    중앙 stocks 구조가 기준이지만, 사용자가 선택하면 구형 txt도 검사한다.
    """
    if not base_stock_path.exists():
        return []

    stocks: list[dict[str, object]] = []
    for line_no, raw_line in enumerate(base_stock_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            stocks.append(
                {
                    "line_no": line_no,
                    "raw_line": line,
                    "code": "",
                    "name": line,
                    "routines": [],
                    "format_error": True,
                }
            )
            continue

        stocks.append(
            {
                "line_no": line_no,
                "raw_line": line,
                "code": parts[0],
                "name": parts[1],
                "routines": [part for part in parts[2:] if part],
                "format_error": False,
            }
        )

    return stocks


def read_json_safely(path: Path) -> tuple[bool, object | None, str]:
    try:
        return True, json.loads(path.read_text(encoding="utf-8")), ""
    except Exception as exc:
        return False, None, str(exc)


def safe_json_dict(path: Path) -> dict[str, Any]:
    ok, data, _ = read_json_safely(path)
    return data if ok and isinstance(data, dict) else {}


def make_integrity_issue(
    category: str,
    location: str,
    message: str,
    action: str,
    handled: str = "미처리",
) -> dict[str, str]:
    return {
        "category": category,
        "location": location,
        "message": message,
        "action": action,
        "handled": handled,
    }


def write_invalid_items_log(
    issues: list[dict[str, str]],
    invalid_items_log_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("")
    lines.append(f"[{now_text()}]")
    lines.append(f"검출항목: {len(issues)}건")

    if not issues:
        lines.append("내용: 무결성검증 결과 이상 없음")
    else:
        for index, issue in enumerate(issues, start=1):
            lines.append(f"{index}.")
            lines.append(f"   구분: {issue.get('category', '')}")
            lines.append(f"   위치: {issue.get('location', '')}")
            lines.append(f"   문제 내용: {issue.get('message', '')}")
            lines.append(f"   권장 조치: {issue.get('action', '')}")
            lines.append(f"   상태: {issue.get('handled', '미처리')}")

    invalid_items_log_path.open("a", encoding="utf-8").write("\n".join(lines) + "\n")


def get_registered_routine_names(project_root: Path) -> set[str]:
    """신규 routines 패키지 기준 루틴명 집합."""
    names: set[str] = set()

    if routine_names is not None:
        try:
            names.update(normalize_routine_name(name) for name in routine_names())  # type: ignore[misc]
            return {name for name in names if name}
        except Exception:
            pass

    routines_root = project_root / "routines"
    if not routines_root.exists():
        return set()

    for package_dir in routines_root.iterdir():
        if not package_dir.is_dir():
            continue
        meta_path = package_dir / "routine.json"
        if not meta_path.exists():
            continue
        meta = safe_json_dict(meta_path)
        name = normalize_routine_name(meta.get("name") or package_dir.name)
        if name:
            names.add(name)
    return names


def get_routine_records_for_check(project_root: Path) -> list[dict[str, Any]]:
    """무결성검사용 루틴 레코드. gui_routine_registry가 있으면 그것을 우선한다."""
    records: list[dict[str, Any]] = []

    if get_routine_records is not None:
        try:
            for record in get_routine_records():  # type: ignore[misc]
                records.append(
                    {
                        "name": normalize_routine_name(getattr(record, "name", "")),
                        "path": Path(getattr(record, "path")),
                        "enabled": bool(getattr(record, "enabled", True)),
                        "valid": bool(getattr(record, "valid", False)),
                        "entry_file": str(getattr(record, "entry_file", "routine.py") or "routine.py"),
                        "problem": str(getattr(record, "problem", "") or ""),
                        "metadata": getattr(record, "metadata", {}) if isinstance(getattr(record, "metadata", {}), dict) else {},
                    }
                )
            return records
        except Exception:
            records.clear()

    routines_root = project_root / "routines"
    if not routines_root.exists():
        return []

    for package_dir in sorted(routines_root.iterdir(), key=lambda path: decode_hash_unicode(path.name)):
        if not package_dir.is_dir():
            continue
        meta_path = package_dir / "routine.json"
        if not meta_path.exists():
            continue
        meta = safe_json_dict(meta_path)
        name = normalize_routine_name(meta.get("name") or package_dir.name)
        entry_file = str(meta.get("entry_file") or "routine.py").strip() or "routine.py"
        entry_path = package_dir / entry_file
        records.append(
            {
                "name": name,
                "path": package_dir,
                "enabled": bool(meta.get("enabled", True)),
                "valid": bool(entry_path.exists() and entry_path.is_file()),
                "entry_file": entry_file,
                "problem": "" if entry_path.exists() else f"entry_file missing: {entry_file}",
                "metadata": meta,
            }
        )
    return records


def get_central_stock_dirs(project_root: Path) -> list[Path]:
    stocks_root = project_root / "stocks"
    if not stocks_root.exists() or not stocks_root.is_dir():
        return []
    result = [path for path in stocks_root.iterdir() if path.is_dir() and not path.name.startswith(".")]
    return sorted(result, key=lambda path: decode_hash_unicode(path.name))


def parse_stock_folder_name(stock_dir: Path) -> tuple[str, str, bool]:
    decoded = decode_hash_unicode(stock_dir.name)
    parts = decoded.split("_", 1)
    if len(parts) != 2:
        return "", decoded, False
    return parts[0].strip(), parts[1].strip(), True


def extract_routines_from_config(config: dict[str, Any]) -> list[str]:
    routines: list[str] = []
    raw_routines = config.get("routines")
    if isinstance(raw_routines, list):
        for item in raw_routines:
            name = normalize_routine_name(item)
            if name and name not in routines:
                routines.append(name)

    for key in ("routine", "routine_name", "assigned_routine", "active_routine"):
        name = normalize_routine_name(config.get(key))
        if name and name not in routines:
            routines.append(name)

    return routines


def run_integrity_checks(
    selected_checks: set[str],
    project_root: Path,
    base_stock_path: Path,
    stock_library_path: Path,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    base_stocks = read_base_stocks_for_check(base_stock_path)
    library = load_stock_library(stock_library_path)
    library_by_code = {stock.get("code", "").strip(): stock for stock in library}
    registered_routines = get_registered_routine_names(project_root)
    routine_records = get_routine_records_for_check(project_root)
    stock_dirs = get_central_stock_dirs(project_root)

    seen_codes: dict[str, int] = {}
    seen_names: dict[str, int] = {}

    # 1) 구형 기초종목.txt 호환 검사
    for stock in base_stocks:
        line_no = int(stock.get("line_no", 0))
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "")).strip()
        routines = [normalize_routine_name(item) for item in stock.get("routines", []) if normalize_routine_name(item)]

        if stock.get("format_error"):
            issues.append(
                make_integrity_issue(
                    "기초종목",
                    f"기초종목.txt {line_no}행",
                    "행 형식 오류",
                    "종목코드,종목명 형식 확인",
                )
            )
            continue

        if "base_duplicate" in selected_checks:
            if code in seen_codes:
                issues.append(
                    make_integrity_issue(
                        "중복",
                        f"기초종목.txt {line_no}행",
                        f"중복 코드: {code}",
                        f"{seen_codes[code]}행과 중복 여부 확인",
                    )
                )
            else:
                seen_codes[code] = line_no

            if name in seen_names:
                issues.append(
                    make_integrity_issue(
                        "중복",
                        f"기초종목.txt {line_no}행",
                        f"중복 종목명: {name}",
                        f"{seen_names[name]}행과 중복 여부 확인",
                    )
                )
            else:
                seen_names[name] = line_no

        if "stock_code" in selected_checks and not is_valid_stock_code(code):
            issues.append(
                make_integrity_issue(
                    "코드오류",
                    f"기초종목.txt {line_no}행",
                    "종목코드 오류",
                    "6자리 숫자 여부 확인",
                )
            )

        if "stock_name" in selected_checks:
            if not name:
                issues.append(
                    make_integrity_issue(
                        "종목명오류",
                        f"기초종목.txt {line_no}행",
                        "종목명 공백",
                        "종목명 입력 확인",
                    )
                )
            else:
                library_stock = library_by_code.get(code)
                if library_stock is None:
                    issues.append(
                        make_integrity_issue(
                            "라이브러리",
                            f"기초종목.txt {line_no}행",
                            "라이브러리 없음",
                            "stock_library.json 확인",
                        )
                    )
                elif name != str(library_stock.get("name", "")).strip():
                    issues.append(
                        make_integrity_issue(
                            "라이브러리",
                            f"기초종목.txt {line_no}행",
                            "종목명 불일치",
                            "stock_library.json 기준 확인",
                        )
                    )

        if "routine_folder" in selected_checks:
            for routine_name in routines:
                if routine_name not in registered_routines:
                    issues.append(
                        make_integrity_issue(
                            "루틴오류",
                            f"기초종목.txt {line_no}행",
                            f"루틴 패키지 없음: {routine_name}",
                            f"routines/{routine_name}/routine.json 확인",
                        )
                    )

    # 2) 신규 루틴 패키지 검사. 기존 UI 키 budget_json은 routine.json 검사로 호환 처리한다.
    if "budget_json" in selected_checks:
        if not routine_records:
            issues.append(
                make_integrity_issue(
                    "루틴",
                    "routines/",
                    "루틴 패키지 없음",
                    "routines/<루틴명>/routine.json 생성 확인",
                )
            )

        for record in routine_records:
            routine_name = str(record.get("name", "")).strip()
            routine_path = Path(record.get("path"))
            meta_path = routine_path / "routine.json"
            entry_file = str(record.get("entry_file") or "routine.py")
            entry_path = routine_path / entry_file

            if not routine_name:
                issues.append(
                    make_integrity_issue(
                        "루틴",
                        str(routine_path.relative_to(project_root)),
                        "루틴명 없음",
                        "routine.json name 확인",
                    )
                )

            ok, data, _ = read_json_safely(meta_path)
            if not ok:
                issues.append(
                    make_integrity_issue(
                        "routine.json",
                        str(meta_path.relative_to(project_root)),
                        "routine.json 오류",
                        "JSON 문법 확인",
                    )
                )
            elif not isinstance(data, dict):
                issues.append(
                    make_integrity_issue(
                        "routine.json",
                        str(meta_path.relative_to(project_root)),
                        "routine.json 형식 오류",
                        "객체 형식 확인",
                    )
                )

            if not entry_path.exists():
                issues.append(
                    make_integrity_issue(
                        "루틴파일",
                        str(entry_path.relative_to(project_root)),
                        "루틴 진입 파일 없음",
                        "routine.json entry_file 및 routine.py 존재 확인",
                    )
                )

    # 3) 중앙 stocks 구조 검사
    for stock_dir in stock_dirs:
        code, name, folder_ok = parse_stock_folder_name(stock_dir)

        if not folder_ok:
            issues.append(
                make_integrity_issue(
                    "폴더명",
                    str(stock_dir.relative_to(project_root)),
                    "종목 폴더명 형식 오류",
                    "종목코드_종목명 형식 확인",
                )
            )
            continue

        if "stock_code" in selected_checks and not is_valid_stock_code(code):
            issues.append(
                make_integrity_issue(
                    "코드오류",
                    str(stock_dir.relative_to(project_root)),
                    f"종목코드 오류: {code}",
                    "6자리 숫자 여부 확인",
                )
            )

        if "stock_name" in selected_checks and not name:
            issues.append(
                make_integrity_issue(
                    "종목명오류",
                    str(stock_dir.relative_to(project_root)),
                    "종목명 공백",
                    "종목 폴더명 확인",
                )
            )

        if "required_files" in selected_checks:
            required_paths = [
                stock_dir / "config.json",
                stock_dir / "state.json",
                stock_dir / "orders.json",
                stock_dir / "logs",
            ]
            for required_path in required_paths:
                if not required_path.exists():
                    issues.append(
                        make_integrity_issue(
                            "파일누락",
                            str(required_path.relative_to(project_root)),
                            "필수 파일 없음",
                            "중앙 stocks 종목 구조 확인",
                        )
                    )

        json_targets = [
            ("config_json", "config", stock_dir / "config.json"),
            ("state_json", "state", stock_dir / "state.json"),
            ("orders_json", "orders", stock_dir / "orders.json"),
        ]

        config_data: dict[str, Any] = {}
        for check_key, category, json_path in json_targets:
            if not json_path.exists():
                continue

            ok, data, _ = read_json_safely(json_path)
            if not ok:
                if check_key in selected_checks:
                    issues.append(
                        make_integrity_issue(
                            category,
                            str(json_path.relative_to(project_root)),
                            f"{json_path.name} 오류",
                            "JSON 문법 확인",
                        )
                    )
                continue

            if not isinstance(data, dict):
                if check_key in selected_checks:
                    issues.append(
                        make_integrity_issue(
                            category,
                            str(json_path.relative_to(project_root)),
                            f"{json_path.name} 형식 오류",
                            "객체 형식 확인",
                        )
                    )
                continue

            if check_key == "config_json":
                config_data = data

        if "routine_folder" in selected_checks and config_data:
            assigned_routines = extract_routines_from_config(config_data)
            for routine_name in assigned_routines:
                if routine_name not in registered_routines:
                    issues.append(
                        make_integrity_issue(
                            "루틴오류",
                            str((stock_dir / "config.json").relative_to(project_root)),
                            f"지정 루틴 패키지 없음: {routine_name}",
                            f"routines/{routine_name}/routine.json 확인 또는 종목 루틴 재지정",
                        )
                    )

    return issues
