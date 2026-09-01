# -*- coding: utf-8 -*-
"""
gui_auto_trade_unregister.py

자동매매설정창의 등록해제 처리 헬퍼.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import QMessageBox

from gui_auto_trade_utils import auto_trade_unregister_category
from gui_base_stock_service import update_base_stock_routines as update_base_stock_routines_from_service
from gui_toast import show_toast
from gui_operation_ui_context import (
    refresh_auto_trade_views,
    sync_auto_trade_monitoring_universe,
)
from gui_operation_ui_context import operation_dialog_parent
from assignment_authorization_service import (
    ASSIGNMENT_INTENT_STOCK_UNREGISTER,
    execute_assignment_unassign,
    inspect_stock_unregister_availability,
)
from gui_user_reason import user_reason_message

PROJECT_ROOT = Path(__file__).resolve().parent
BASE_STOCK_PATH = PROJECT_ROOT / "기초종목.txt"


def unregister_result_toast_text(
    success_count: int,
    blocked_count: int,
    blocked_names: list[str] | None = None,
) -> str:
    success = max(int(success_count), 0)
    failed = max(int(blocked_count), 0)
    lines = [f"루틴 해제 성공 {success}종목 / 실패 {failed}종목"]
    details = [str(value or "").strip() for value in blocked_names or () if str(value or "").strip()]
    lines.extend(f"- {detail}" for detail in details[:5])
    if len(details) > 5:
        lines.append(f"- 그 외 {len(details) - 5}종목")
    return "\n".join(lines)
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_changelog(change_type: str, filename: str, message: str) -> None:
    block = (
        f"\n[{now_text()}]\n"
        f"버전: v1.1\n"
        f"구분: {change_type}\n"
        f"파일: {filename}\n"
        f"내용: {message}\n"
        f"작성자: admin\n"
    )
    with CHANGELOG_PATH.open("a", encoding="utf-8") as file:
        file.write(block)


def update_base_stock_routines(
    code: str,
    name: str,
    routines: list[str],
    *,
    expected_instance_id: str | None = None,
) -> bool:
    """
    등록해제 루틴 연결 갱신.

    중앙 종목관리 개편 이후 이 파일 안에서 기초종목.txt를 직접 수정하지 않는다.
    gui_base_stock_service.update_base_stock_routines()로 위임하여
    - stocks/ 중앙 구조가 있으면 stocks/종목/config.json 갱신
    - 아직 stocks/가 없으면 기존 기초종목.txt fallback
    흐름을 동일하게 사용한다.
    """
    return bool(
        update_base_stock_routines_from_service(
            code,
            name,
            routines,
            expected_instance_id=expected_instance_id,
        )
    )



def unregister_selected_auto_trade_stocks(window) -> None:
    """
    자동매매설정 창에서 선택 종목을 현재 루틴에서 등록해제한다.

    정책:
    - 기초종목.txt의 루틴 연결만 제거한다. 종목 자체는 기초종목에 남긴다.
    - 루틴 runtime 폴더, config.json, logs는 유지한다.
    - 정지/감시중 + 보유·미체결 없음은 즉시 등록해제한다.
    - 보유·미체결, 운영 중, 긴급정지, 검토관리, 무결성 오류는 등록해제하지 않는다.
    - 차단 종목의 state.json과 orders.json은 변경하지 않는다.
    """
    message_parent = operation_dialog_parent(window)
    selected = window.selected_stock_infos()
    if not selected:
        QMessageBox.warning(message_parent, "선택 오류", "등록해제할 종목을 1개 이상 선택하세요.")
        return
    routine_name = window.current_selected_routine_name()
    if not routine_name and bool(getattr(window, "_all_stocks_scope_active", False)):
        routine_name = "전체"
    if not routine_name:
        routine_name = "-"

    immediate_items: list[dict[str, object]] = []
    blocked_items: list[dict[str, object]] = []
    failure_details: list[str] = []
    seen: set[tuple[str, str]] = set()

    for stock_dir, code, name in selected:
        key = (code, name)
        if key in seen:
            continue
        seen.add(key)
        availability = inspect_stock_unregister_availability(
            window,
            PROJECT_ROOT,
            code,
            name,
        )
        if not availability.allowed:
            item = {
                "category": "blocked",
                "code": code,
                "name": name,
                "title": f"{code} {name}",
                "instance_id": availability.current_instance_id,
                "reasons": [availability.reason_code],
            }
            failure_details.append(
                f"{name or code}: "
                + user_reason_message(
                    availability.reason_code,
                    fallback="현재 상태에서는 루틴에서 해제할 수 없습니다.",
                )
            )
        else:
            item = auto_trade_unregister_category(routine_name, stock_dir, code, name)
        category = str(item.get("category", "blocked"))
        if category == "immediate":
            immediate_items.append(item)
        else:
            blocked_items.append(item)
            if availability.allowed:
                reasons = item.get("reasons", [])
                reason = next(
                    (
                        str(value).strip()
                        for value in reasons
                        if str(value or "").strip()
                    ),
                    "현재 상태에서는 루틴에서 해제할 수 없습니다.",
                )
                failure_details.append(f"{name or code}: {reason}")

    process_items = immediate_items
    if not process_items:
        show_toast(
            message_parent,
            unregister_result_toast_text(
                0,
                len(blocked_items),
                failure_details,
            ),
        )
        return
    completed_items: list[str] = []
    failed_names: list[str] = list(failure_details)

    for item in process_items:
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        if not code or not name:
            continue

        expected_instance_id = str(item.get("instance_id", "") or "").strip()
        result = execute_assignment_unassign(
            window,
            PROJECT_ROOT,
            code,
            name,
            expected_instance_id=expected_instance_id,
            intent=ASSIGNMENT_INTENT_STOCK_UNREGISTER,
        )
        if result.ok and result.changed:
            completed_items.append(f"{code},{name}")
        elif not result.ok:
            failed_names.append(
                f"{name or code}: "
                + user_reason_message(
                    getattr(result, "reason_code", ""),
                    fallback="루틴 해제 저장을 완료하지 못했습니다.",
                )
            )

    if not completed_items:
        show_toast(
            message_parent,
            unregister_result_toast_text(
                0,
                len(failed_names),
                failed_names,
            ),
        )
        return
    append_changelog(
        "UPDATE",
        "종목 루틴 연결",
        f"자동매매설정 창 루틴 등록해제: {' / '.join(completed_items)} / 종목 정보 갱신",
    )

    window.statusBar_message(f"루틴 등록해제 완료: {len(completed_items)}개")
    sync_auto_trade_monitoring_universe(window)
    refresh_auto_trade_views(window)
    show_toast(
        message_parent,
        unregister_result_toast_text(
            len(completed_items),
            len(failed_names),
            failed_names,
        ),
    )
    return
