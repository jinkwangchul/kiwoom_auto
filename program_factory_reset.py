# -*- coding: utf-8 -*-
"""Program factory reset with an explicit, project-local manifest."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from gui_auto_trade_integrity import is_emergency_stopped_state, is_review_required_state
from gui_order_utils import pending_order_side_quantities
from startup_runtime_initializer import (
    STATUS_INITIALIZED,
    initialize_pristine_startup_runtime,
)


PROJECT_ROOT = Path(__file__).resolve().parent

DELETE_CONTENTS = (
    "stocks",
    "routine_instances",
    "runtime",
    "archived_stocks",
    "artifacts",
    "reports",
)
DELETE_FILES = ("invalid_items.log",)
ROUTINE_DERIVED_FILES = ("approval_session.json",)
ROUTINE_DERIVED_DIRS = ("reports",)
RESET_FILES = ("operation_policy.json", "global_schedule.json")
PRESERVE_PATHS = (
    "routines",
    "_등록확인폴더",
    "_지표추종매매",
    "stock_library.json",
    "screen_registry.json",
    "기초종목.txt",
    "PROJECT_CHANGELOG.txt",
)

_ACTIVE_STOCK_STATUSES = {
    "RUNNING",
    "STARTED",
    "AUTO",
    "TRADING",
    "AUTO_CLOSE",
    "AUTO_CLOSING",
    "EARLY_CLOSE",
    "EARLY_CLOSING",
    "LIQUIDATING",
    "CLOSING",
    "SELL_ONLY",
    "WATCH_SELL",
    "BUY_SUSPENDED",
    "BUY_STOPPED",
}
_KNOWN_STOCK_STATUSES = {
    "",
    "STOP",
    "STOPPED",
    "MONITORING",
    "WATCHING",
    "REVIEW",
    "REVIEW_REQUIRED",
    "EMERGENCY",
    "EMERGENCY_STOP",
    "EMERGENCY_STOPPED",
    *_ACTIVE_STOCK_STATUSES,
}
_ACTIVE_OPERATION_STATUSES = {"RUNNING", "CLOSING"}
_TERMINAL_EXECUTION_STATUSES = {
    "FILLED",
    "COMPLETED",
    "COMPLETE",
    "CANCELED",
    "CANCELLED",
    "REJECTED",
    "FAILED",
    "EXPIRED",
}
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def factory_reset_manifest() -> dict[str, tuple[str, ...]]:
    return {
        "DELETE_CONTENTS": DELETE_CONTENTS,
        "DELETE_FILES": DELETE_FILES,
        "DELETE_ROUTINE_DERIVED_FILES": ROUTINE_DERIVED_FILES,
        "DELETE_ROUTINE_DERIVED_DIRS": ROUTINE_DERIVED_DIRS,
        "RESET": RESET_FILES,
        "PRESERVE": PRESERVE_PATHS,
    }


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.stat(), "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return False


def _result(success: bool, *, issues: list[str] | None = None) -> dict[str, Any]:
    return {
        "success": success,
        "issues": list(issues or []),
        "manifest": factory_reset_manifest(),
    }


def _project_child(root: Path, relative: str) -> Path:
    resolved_root = root.resolve(strict=True)
    candidate = root / relative
    if candidate.exists() and _is_link_or_reparse(candidate):
        raise RuntimeError(f"심볼릭 링크 경로는 초기화할 수 없습니다: {relative}")
    resolved = candidate.resolve(strict=False)
    if resolved.parent != resolved_root:
        raise RuntimeError(f"허용되지 않은 초기화 경로입니다: {relative}")
    return candidate


def _read_json_object(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"{path.name} 읽기 실패: {exc}"
    if not isinstance(data, dict):
        return {}, f"{path.name} 최상위 형식 오류"
    return data, ""


def _positive_int(value: object) -> tuple[int, bool]:
    try:
        parsed = int(str(value or "0").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0, False
    return max(parsed, 0), True


def _stock_safety_issues(stocks_root: Path) -> list[str]:
    if not stocks_root.exists():
        return []
    if not stocks_root.is_dir() or _is_link_or_reparse(stocks_root):
        return ["등록 종목 저장 경로를 안전하게 확인할 수 없습니다."]

    issues: list[str] = []
    for stock_dir in sorted(path for path in stocks_root.iterdir() if path.is_dir()):
        if _is_link_or_reparse(stock_dir):
            issues.append(f"{stock_dir.name}: 심볼릭 링크 종목 경로")
            continue
        state_path = stock_dir / "state.json"
        if not state_path.exists():
            issues.append(f"{stock_dir.name}: state.json 없음")
            continue
        state, state_issue = _read_json_object(state_path)
        if state_issue:
            issues.append(f"{stock_dir.name}: {state_issue}")
            continue
        status = str(state.get("status") or "STOPPED").strip().upper()
        if status not in _KNOWN_STOCK_STATUSES:
            issues.append(f"{stock_dir.name}: 알 수 없는 운영 상태")
        if is_review_required_state(state):
            issues.append(f"{stock_dir.name}: 검토관리 상태")
        if is_emergency_stopped_state(state):
            issues.append(f"{stock_dir.name}: 긴급정지 상태")
        if status in _ACTIVE_STOCK_STATUSES:
            issues.append(f"{stock_dir.name}: 운영 또는 마감 진행 중")
        holding_qty, holding_ok = _positive_int(state.get("holding_qty", 0))
        if not holding_ok:
            issues.append(f"{stock_dir.name}: 보유수량 확인 실패")
        elif holding_qty > 0:
            issues.append(f"{stock_dir.name}: 보유수량 {holding_qty}")
        orders_path = stock_dir / "orders.json"
        if not orders_path.exists():
            issues.append(f"{stock_dir.name}: orders.json 없음")
            continue
        orders, orders_issue = _read_json_object(orders_path)
        if orders_issue:
            issues.append(f"{stock_dir.name}: {orders_issue}")
            continue
        if not isinstance(orders.get("orders"), list):
            issues.append(f"{stock_dir.name}: orders.json 형식 오류")
            continue
        buy_pending, sell_pending = pending_order_side_quantities(stock_dir, state)
        if buy_pending == "?" or sell_pending == "?":
            issues.append(f"{stock_dir.name}: 미체결 상태 확인 실패")
        elif int(buy_pending) > 0 or int(sell_pending) > 0:
            issues.append(f"{stock_dir.name}: 미체결 주문 존재")
    return issues


def _runtime_safety_issues(runtime_root: Path) -> list[str]:
    if not runtime_root.exists():
        return []
    if not runtime_root.is_dir() or _is_link_or_reparse(runtime_root):
        return ["Runtime 저장 경로를 안전하게 확인할 수 없습니다."]

    issues: list[str] = []
    operation_state, issue = _read_json_object(runtime_root / "operation_state.json")
    if issue:
        issues.append(issue)
    elif str(operation_state.get("operation_status") or "").strip().upper() in _ACTIVE_OPERATION_STATUSES:
        issues.append("전체 운영이 진행 중입니다.")

    list_contracts = {
        "order_queue.json": "orders",
        "positions.json": "positions",
        "broker_holdings.json": "holdings",
        "order_locks.json": "locks",
    }
    for filename, field in list_contracts.items():
        data, read_issue = _read_json_object(runtime_root / filename)
        if read_issue:
            issues.append(read_issue)
            continue
        if not data:
            continue
        items = data.get(field)
        if not isinstance(items, list):
            issues.append(f"{filename}.{field} 형식 오류")
            continue
        if filename in {"positions.json", "broker_holdings.json"}:
            for item in items:
                if not isinstance(item, dict):
                    issues.append(f"{filename} 항목 형식 오류")
                    break
                quantity = next(
                    (item.get(key) for key in ("quantity", "qty", "holding_qty", "보유수량") if key in item),
                    0,
                )
                parsed, valid = _positive_int(quantity)
                if not valid or parsed > 0:
                    issues.append(f"{filename}: 보유 상태가 남아 있습니다.")
                    break
        elif items:
            issues.append(f"{filename}: 진행 중인 주문 또는 잠금이 남아 있습니다.")

    executions, read_issue = _read_json_object(runtime_root / "order_executions.json")
    if read_issue:
        issues.append(read_issue)
    elif executions:
        items = executions.get("executions")
        if not isinstance(items, list):
            issues.append("order_executions.json.executions 형식 오류")
        else:
            for item in items:
                if not isinstance(item, dict):
                    issues.append("order_executions.json 항목 형식 오류")
                    break
                status = str(item.get("status") or "").strip().upper()
                if status not in _TERMINAL_EXECUTION_STATUSES:
                    issues.append("완료되지 않은 주문 실행 기록이 남아 있습니다.")
                    break
    return issues


def validate_factory_reset_safety(
    project_root: str | Path = PROJECT_ROOT,
    *,
    broker_connected: bool,
) -> dict[str, Any]:
    root = Path(project_root)
    try:
        root = root.resolve(strict=True)
        for relative in DELETE_CONTENTS:
            _project_child(root, relative)
        for relative in DELETE_FILES + RESET_FILES:
            _project_child(root, relative)
    except Exception as exc:
        return _result(False, issues=[str(exc)])

    issues: list[str] = []
    if broker_connected:
        issues.append("키움 서버 연결을 먼저 종료해 주세요.")
    issues.extend(_stock_safety_issues(root / "stocks"))
    issues.extend(_runtime_safety_issues(root / "runtime"))
    return _result(not issues, issues=issues)


def _clear_directory_contents(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in list(path.iterdir()):
        if _is_link_or_reparse(child):
            raise RuntimeError(f"심볼릭 링크는 초기화할 수 없습니다: {child}")
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def execute_program_factory_reset(
    project_root: str | Path = PROJECT_ROOT,
    *,
    broker_connected: bool,
) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=True)
    safety = validate_factory_reset_safety(root, broker_connected=broker_connected)
    if not safety["success"]:
        return safety

    try:
        for relative in DELETE_CONTENTS:
            _clear_directory_contents(_project_child(root, relative))

        for relative in DELETE_FILES:
            path = _project_child(root, relative)
            if path.exists():
                if _is_link_or_reparse(path) or not path.is_file():
                    raise RuntimeError(f"허용되지 않은 파일 경로입니다: {relative}")
                path.unlink()

        routines_root = _project_child(root, "routines")
        if routines_root.exists():
            for routine_dir in sorted(path for path in routines_root.iterdir() if path.is_dir()):
                if _is_link_or_reparse(routine_dir):
                    raise RuntimeError(f"부모 루틴 심볼릭 링크는 처리할 수 없습니다: {routine_dir.name}")
                for filename in ROUTINE_DERIVED_FILES:
                    path = routine_dir / filename
                    if path.exists():
                        path.unlink()
                for dirname in ROUTINE_DERIVED_DIRS:
                    path = routine_dir / dirname
                    if path.exists():
                        if _is_link_or_reparse(path) or not path.is_dir():
                            raise RuntimeError(f"부모 루틴 파생 경로를 확인할 수 없습니다: {path}")
                        shutil.rmtree(path)

        from gui_operation_environment import default_operation_policy, write_operation_policy
        from state_policy import write_global_schedule

        write_operation_policy(default_operation_policy(), path=root / "operation_policy.json")
        write_global_schedule("09:00:00", "13:30:00", path=root / "global_schedule.json")
        runtime_result = initialize_pristine_startup_runtime(root / "runtime")
        if runtime_result.get("status") != STATUS_INITIALIZED:
            raise RuntimeError(
                "빈 Runtime 기본 구조 생성 실패: "
                + ", ".join(str(item) for item in runtime_result.get("issues", []))
            )

        if any(any((root / name).iterdir()) for name in ("stocks", "routine_instances")):
            raise RuntimeError("등록 종목 또는 사용자 루틴 인스턴스 제거 확인 실패")
    except Exception as exc:
        return _result(False, issues=[f"프로그램 초기화 실패: {exc}"])
    return _result(True)
