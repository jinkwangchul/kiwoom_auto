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
def read_json_safely(path: Path) -> tuple[bool, object | None, str]:
    try:
        return True, json.loads(path.read_text(encoding="utf-8")), ""
    except Exception as exc:
        return False, None, str(exc)


def safe_json_dict(path: Path) -> dict[str, Any]:
    ok, data, _ = read_json_safely(path)
    return data if ok and isinstance(data, dict) else {}
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


LOCAL_STATUS_PASS = "PASS"
LOCAL_STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
LOCAL_STATUS_CHECK_ERROR = "CHECK_ERROR"
SERVER_STATUS_NOT_CHECKED = "SERVER_NOT_CHECKED"

LOCAL_INTEGRITY_SCOPE = "local_stock_integrity"

REVIEW_REQUIRED_ISSUES = {
    "STOCK_FOLDER_IDENTITY",
    "STOCK_CODE_FORMAT",
    "STOCK_NAME_PRESENT",
    "REQUIRED_PATH_MISSING",
    "JSON_ROOT_TYPE_INVALID",
    "ORDERS_REQUIRED_KEY_MISSING",
    "ORDER_STRUCTURE_INVALID",
    "ROUTINE_ASSIGNMENT_INVALID",
    "ROUTINE_ENTRY_FILE_MISSING",
}

CHECK_ERROR_ISSUES = {
    "NO_STOCK_TARGETS",
    "STOCK_TARGET_ACCESS_ERROR",
    "JSON_READ_ERROR",
}


def _safe_relative_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _standard_issue(
    *,
    issue_code: str,
    message: str,
    recommended_action: str,
    source_path: Path | str,
    project_root: Path,
    stock_code: str = "",
    stock_name: str = "",
    stock_dir: Path | str | None = None,
    severity: str | None = None,
    execution_status: str | None = None,
    checked_at: str,
) -> dict[str, object]:
    requires_review = issue_code in REVIEW_REQUIRED_ISSUES
    if execution_status is None:
        execution_status = (
            LOCAL_STATUS_CHECK_ERROR
            if issue_code in CHECK_ERROR_ISSUES
            else LOCAL_STATUS_REVIEW_REQUIRED
        )
    if severity is None:
        severity = "ERROR" if execution_status == LOCAL_STATUS_CHECK_ERROR else "REVIEW"
    path_text = (
        _safe_relative_path(source_path, project_root)
        if isinstance(source_path, Path)
        else str(source_path)
    )
    stock_dir_text = ""
    if stock_dir is not None:
        stock_dir_text = (
            _safe_relative_path(stock_dir, project_root)
            if isinstance(stock_dir, Path)
            else str(stock_dir)
        )
    return {
        "check_scope": LOCAL_INTEGRITY_SCOPE,
        "execution_status": execution_status,
        "stock_code": str(stock_code or ""),
        "stock_name": str(stock_name or ""),
        "stock_dir": stock_dir_text,
        "issue_code": issue_code,
        "severity": severity,
        "message": message,
        "recommended_action": recommended_action,
        "requires_review": requires_review,
        "checked_at": checked_at,
        "source_path": path_text,
        "server_checked": False,
    }


def _standard_result(
    *,
    started_at: str,
    completed_at: str,
    checked_stock_count: int,
    issues: list[dict[str, object]],
) -> dict[str, object]:
    review_required_count = sum(1 for issue in issues if issue.get("requires_review") is True)
    check_error_count = sum(
        1 for issue in issues
        if issue.get("execution_status") == LOCAL_STATUS_CHECK_ERROR
    )
    if check_error_count:
        local_status = LOCAL_STATUS_CHECK_ERROR
    elif review_required_count:
        local_status = LOCAL_STATUS_REVIEW_REQUIRED
    else:
        local_status = LOCAL_STATUS_PASS
    return {
        "local_status": local_status,
        "server_status": SERVER_STATUS_NOT_CHECKED,
        "checked_stock_count": checked_stock_count,
        "review_required_count": review_required_count,
        "check_error_count": check_error_count,
        "server_not_checked_count": 0,
        "issues": issues,
        "started_at": started_at,
        "completed_at": completed_at,
    }


def _result_with_recalculated_counts(
    result: dict[str, object],
    issues: list[dict[str, object]],
) -> dict[str, object]:
    review_required_count = sum(1 for issue in issues if issue.get("requires_review") is True)
    check_error_count = sum(
        1 for issue in issues
        if issue.get("execution_status") == LOCAL_STATUS_CHECK_ERROR
    )
    if check_error_count:
        local_status = LOCAL_STATUS_CHECK_ERROR
    elif review_required_count:
        local_status = LOCAL_STATUS_REVIEW_REQUIRED
    else:
        local_status = LOCAL_STATUS_PASS

    updated = dict(result)
    updated["local_status"] = local_status
    updated["review_required_count"] = review_required_count
    updated["check_error_count"] = check_error_count
    updated["issues"] = issues
    updated["completed_at"] = now_text()
    return updated


def _issue_stock_dir(issue: dict[str, object], project_root: Path) -> Path | None:
    raw_stock_dir = str(issue.get("stock_dir", "") or "").strip()
    if raw_stock_dir:
        stock_dir = Path(raw_stock_dir)
        return stock_dir if stock_dir.is_absolute() else project_root / stock_dir

    raw_source_path = str(issue.get("source_path", "") or "").strip()
    if not raw_source_path:
        return None
    source_path = Path(raw_source_path)
    parts = source_path.parts
    if len(parts) >= 2 and parts[0] == "stocks":
        return project_root / parts[0] / parts[1]
    if source_path.is_absolute():
        try:
            relative = source_path.relative_to(project_root)
        except ValueError:
            return None
        if len(relative.parts) >= 2 and relative.parts[0] == "stocks":
            return project_root / relative.parts[0] / relative.parts[1]
    return None


def _integrity_review_reason(issue: dict[str, object]) -> str:
    issue_code = str(issue.get("issue_code", "") or "").strip()
    message = str(issue.get("message", "") or "").strip()
    return f"[{issue_code}] {message}" if message else f"[{issue_code}]"


def apply_integrity_review_required_issues(
    result: dict[str, object],
    *,
    project_root: Path,
    review_writer,
    source: str = "무결성검사",
) -> dict[str, object]:
    """Apply REVIEW_REQUIRED local integrity issues through the official writer.

    The caller supplies the existing review writer, such as
    AutoTradeSettingWindow.mark_review_required. This adapter does not write
    files directly and does not touch CHECK_ERROR or SERVER_NOT_CHECKED results.
    """
    root = Path(project_root)
    issues = [dict(issue) for issue in result.get("issues", []) if isinstance(issue, dict)]
    grouped: dict[Path, dict[str, object]] = {}

    for issue in issues:
        if issue.get("requires_review") is not True:
            continue
        if str(issue.get("execution_status", "") or "").strip().upper() != LOCAL_STATUS_REVIEW_REQUIRED:
            continue
        issue_code = str(issue.get("issue_code", "") or "").strip()
        if not issue_code:
            continue
        stock_dir = _issue_stock_dir(issue, root)
        if stock_dir is None:
            continue

        bucket = grouped.setdefault(
            stock_dir,
            {
                "stock_code": str(issue.get("stock_code", "") or "").strip(),
                "stock_name": str(issue.get("stock_name", "") or "").strip(),
                "issues_by_code": {},
            },
        )
        issues_by_code = bucket.get("issues_by_code")
        if isinstance(issues_by_code, dict) and issue_code not in issues_by_code:
            issues_by_code[issue_code] = issue
        if not str(bucket.get("stock_code", "") or "").strip():
            bucket["stock_code"] = str(issue.get("stock_code", "") or "").strip()
        if not str(bucket.get("stock_name", "") or "").strip():
            bucket["stock_name"] = str(issue.get("stock_name", "") or "").strip()

    for stock_dir, bucket in grouped.items():
        state = safe_json_dict(stock_dir / "state.json")
        existing_reason = str(
            state.get("review_reason", "") or state.get("review_detail", "") or ""
        ).strip()
        reasons: list[str] = [existing_reason] if existing_reason else []
        issues_by_code = bucket.get("issues_by_code")
        if not isinstance(issues_by_code, dict):
            continue

        for issue_code, issue in issues_by_code.items():
            if f"[{issue_code}]" in existing_reason:
                continue
            if isinstance(issue, dict):
                reasons.append(_integrity_review_reason(issue))

        if len(reasons) == (1 if existing_reason else 0):
            continue

        code = str(bucket.get("stock_code", "") or "").strip()
        name = str(bucket.get("stock_name", "") or "").strip()
        if not code or not name:
            parsed_code, parsed_name, folder_ok = parse_stock_folder_name(stock_dir)
            if folder_ok:
                code = code or parsed_code
                name = name or parsed_name

        item = {
            "routine_name": "",
            "stock_dir": stock_dir,
            "code": code,
            "name": name,
            "review_reasons": reasons,
            "review_location": source,
        }

        try:
            written = bool(review_writer(stock_dir, code, name, item, source=source))
        except Exception as exc:
            written = False
            error_message = str(exc)
        else:
            error_message = "review writer returned false"

        if not written:
            issues.append(
                _standard_issue(
                    issue_code="CHECK_ERROR",
                    message=f"Review writer failed for {code} {name}: {error_message}",
                    recommended_action="Retry review registration from stock management.",
                    source_path=stock_dir,
                    project_root=root,
                    stock_code=code,
                    stock_name=name,
                    stock_dir=stock_dir,
                    execution_status=LOCAL_STATUS_CHECK_ERROR,
                    severity="ERROR",
                    checked_at=now_text(),
                )
            )

    return _result_with_recalculated_counts(result, issues)


def _stock_json_targets(stock_dir: Path) -> list[tuple[str, Path]]:
    return [
        ("config", stock_dir / "config.json"),
        ("state", stock_dir / "state.json"),
        ("orders", stock_dir / "orders.json"),
    ]


def run_local_stock_integrity_check(project_root: Path) -> dict[str, object]:
    """Run the read-only local stock integrity service.

    This service never writes state, orders, changelog, invalid-items logs, or
    review metadata. Server/Kiwoom validation is intentionally out of scope.
    """
    root = Path(project_root)
    started_at = now_text()
    checked_at = started_at
    issues: list[dict[str, object]] = []

    try:
        stock_dirs = get_central_stock_dirs(root)
    except Exception as exc:
        issues.append(
            _standard_issue(
                issue_code="STOCK_TARGET_ACCESS_ERROR",
                message=f"Unable to read stock target directory: {exc}",
                recommended_action="Check project_root/stocks access",
                source_path=root / "stocks",
                project_root=root,
                checked_at=checked_at,
            )
        )
        return _standard_result(
            started_at=started_at,
            completed_at=now_text(),
            checked_stock_count=0,
            issues=issues,
        )

    if not stock_dirs:
        issues.append(
            _standard_issue(
                issue_code="NO_STOCK_TARGETS",
                message="No stock target directories found under project_root/stocks.",
                recommended_action="Check whether central stock folders exist.",
                source_path=root / "stocks",
                project_root=root,
                checked_at=checked_at,
            )
        )
        return _standard_result(
            started_at=started_at,
            completed_at=now_text(),
            checked_stock_count=0,
            issues=issues,
        )

    routine_records = get_routine_records_for_check(root)
    routine_by_name = {
        str(record.get("name", "") or "").strip(): record
        for record in routine_records
        if str(record.get("name", "") or "").strip()
    }

    for stock_dir in stock_dirs:
        code, name, folder_ok = parse_stock_folder_name(stock_dir)
        stock_code = code if folder_ok else ""
        stock_name = name if folder_ok else ""

        if not folder_ok:
            issues.append(
                _standard_issue(
                    issue_code="STOCK_FOLDER_IDENTITY",
                    message="Stock folder name must use <code>_<name> format.",
                    recommended_action="Rename the stock folder or recreate it through stock registration.",
                    source_path=stock_dir,
                    project_root=root,
                    stock_name=name,
                    stock_dir=stock_dir,
                    checked_at=checked_at,
                )
            )
            continue

        if not is_valid_stock_code(code):
            issues.append(
                _standard_issue(
                    issue_code="STOCK_CODE_FORMAT",
                    message=f"Invalid stock code: {code}",
                    recommended_action="Use a six-digit numeric stock code other than 000000.",
                    source_path=stock_dir,
                    project_root=root,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    stock_dir=stock_dir,
                    checked_at=checked_at,
                )
            )

        if not name.strip():
            issues.append(
                _standard_issue(
                    issue_code="STOCK_NAME_PRESENT",
                    message="Stock name is empty.",
                    recommended_action="Use <code>_<name> folder format with a non-empty stock name.",
                    source_path=stock_dir,
                    project_root=root,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    stock_dir=stock_dir,
                    checked_at=checked_at,
                )
            )

        required_paths = [
            stock_dir / "config.json",
            stock_dir / "state.json",
            stock_dir / "orders.json",
            stock_dir / "logs",
        ]
        for required_path in required_paths:
            if not required_path.exists():
                issues.append(
                    _standard_issue(
                        issue_code="REQUIRED_PATH_MISSING",
                        message=f"Required path is missing: {required_path.name}",
                        recommended_action="Recreate the stock runtime structure through stock registration or recovery.",
                        source_path=required_path,
                        project_root=root,
                        stock_code=stock_code,
                        stock_name=stock_name,
                        stock_dir=stock_dir,
                        checked_at=checked_at,
                    )
                )

        config_data: dict[str, Any] = {}
        for target_name, json_path in _stock_json_targets(stock_dir):
            if not json_path.exists():
                continue

            ok, data, error = read_json_safely(json_path)
            if not ok:
                issues.append(
                    _standard_issue(
                        issue_code="JSON_READ_ERROR",
                        message=f"{json_path.name} could not be read as JSON: {error}",
                        recommended_action="Fix UTF-8/JSON syntax before retrying integrity checks.",
                        source_path=json_path,
                        project_root=root,
                        stock_code=stock_code,
                        stock_name=stock_name,
                        stock_dir=stock_dir,
                        checked_at=checked_at,
                    )
                )
                continue

            if not isinstance(data, dict):
                issues.append(
                    _standard_issue(
                        issue_code="JSON_ROOT_TYPE_INVALID",
                        message=f"{json_path.name} root must be an object.",
                        recommended_action="Rewrite the file as a JSON object.",
                        source_path=json_path,
                        project_root=root,
                        stock_code=stock_code,
                        stock_name=stock_name,
                        stock_dir=stock_dir,
                        checked_at=checked_at,
                    )
                )
                continue

            if target_name == "config":
                config_data = data

            if target_name == "orders":
                orders = data.get("orders")
                if "orders" not in data or not isinstance(orders, list):
                    issues.append(
                        _standard_issue(
                            issue_code="ORDERS_REQUIRED_KEY_MISSING",
                            message="orders.json must contain an orders list.",
                            recommended_action="Restore orders.json with an orders list.",
                            source_path=json_path,
                            project_root=root,
                            stock_code=stock_code,
                            stock_name=stock_name,
                            stock_dir=stock_dir,
                            checked_at=checked_at,
                        )
                    )
                elif any(not isinstance(order, dict) for order in orders):
                    issues.append(
                        _standard_issue(
                            issue_code="ORDER_STRUCTURE_INVALID",
                            message="orders.json contains non-object order entries.",
                            recommended_action="Keep only JSON object entries in the orders list.",
                            source_path=json_path,
                            project_root=root,
                            stock_code=stock_code,
                            stock_name=stock_name,
                            stock_dir=stock_dir,
                            checked_at=checked_at,
                        )
                    )

        for routine_name in extract_routines_from_config(config_data):
            record = routine_by_name.get(routine_name)
            if record is None:
                issues.append(
                    _standard_issue(
                        issue_code="ROUTINE_ASSIGNMENT_INVALID",
                        message=f"Assigned routine package does not exist: {routine_name}",
                        recommended_action="Check the stock routine assignment and routines registry.",
                        source_path=stock_dir / "config.json",
                        project_root=root,
                        stock_code=stock_code,
                        stock_name=stock_name,
                        stock_dir=stock_dir,
                        checked_at=checked_at,
                    )
                )
                continue

            routine_path = Path(record.get("path"))
            entry_file = str(record.get("entry_file") or "routine.py").strip() or "routine.py"
            entry_path = routine_path / entry_file
            if not entry_path.exists():
                issues.append(
                    _standard_issue(
                        issue_code="ROUTINE_ENTRY_FILE_MISSING",
                        message=f"Routine entry file is missing: {routine_name}/{entry_file}",
                        recommended_action="Restore the routine entry file or update routine.json.",
                        source_path=entry_path,
                        project_root=root,
                        stock_code=stock_code,
                        stock_name=stock_name,
                        stock_dir=stock_dir,
                        checked_at=checked_at,
                    )
                )

    return _standard_result(
        started_at=started_at,
        completed_at=now_text(),
        checked_stock_count=len(stock_dirs),
        issues=issues,
    )
