# -*- coding: utf-8 -*-
"""
gui_auto_trade_review_ops.py

자동매매설정창의 안정성검사/검토관리 열기 처리 헬퍼.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime

from PyQt5.QtWidgets import QMessageBox

from gui_review_required_window import GlobalReviewRequiredWindow
from gui_review_utils import (
    build_review_required_item,
    review_required_for_start,
)
from runtime_io import read_json_dict


PROJECT_ROOT = Path(__file__).resolve().parent
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


def parse_stock_folder_name(folder_name: str) -> tuple[str, str]:
    """종목 폴더명에서 종목코드와 종목명을 분리한다."""
    parts = str(folder_name).split("_", 1)
    if len(parts) != 2:
        return "", str(folder_name).strip()
    return parts[0].strip(), parts[1].strip()


def assigned_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """루틴 폴더 아래 실제 종목 폴더 목록을 반환한다."""
    if not routine_dir.exists() or not routine_dir.is_dir():
        return []
    result: list[Path] = []
    for child in sorted(routine_dir.iterdir(), key=lambda item: item.name):
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and not child.name.startswith("__")
            and (child / "config.json").exists()
        ):
            result.append(child)
    return result


def auto_trade_run_current_routine_stability_check(window) -> None:
    """현재 선택 루틴의 종목을 자동매매 투입 전 기준으로 점검한다.

    역할:
    - 새로고침 대체 기능이다.
    - 상태를 덮어써서 맞추지 않는다.
    - 문제가 있는 종목은 검토종목으로 이동한다.
    - 정상 종목은 상태를 변경하지 않는다.
    """
    routine_dir = window.current_selected_routine_dir()
    routine_name = window.current_selected_routine_name()

    if routine_dir is None or not routine_name:
        QMessageBox.warning(window, "선택 오류", "안정성검사할 루틴을 선택하세요.")
        return

    stock_dirs = assigned_stock_dirs_in_routine(routine_dir)
    if not stock_dirs:
        window.statusBarMessage("안정성검사 대상 종목 없음")
        return

    normal_count = 0
    review_count = 0
    protected_count = 0
    failed_count = 0

    for stock_dir in stock_dirs:
        code, name = parse_stock_folder_name(stock_dir.name)
        state = read_json_dict(stock_dir / "state.json")
        status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"

        if window.operation_policy_protected_status(status):
            protected_count += 1
            continue

        try:
            review_item = window.pre_start_review_check(routine_name, stock_dir, code, name)
            if review_required_for_start(review_item):
                if window.mark_review_required(stock_dir, code, name, review_item, source="안정성검사"):
                    review_count += 1
                else:
                    failed_count += 1
            else:
                normal_count += 1
        except Exception as exc:
            review_item = build_review_required_item(
                routine_name,
                stock_dir,
                code,
                name,
                [f"안정성검사 실패: {exc}"],
            )
            if window.mark_review_required(stock_dir, code, name, review_item, source="안정성검사"):
                review_count += 1
            else:
                failed_count += 1

    append_changelog(
        "CHECK",
        "state.json",
        (
            f"안정성검사: {routine_name} -> "
            f"전체 {normal_count + review_count + protected_count}개 / "
            f"정상 {normal_count}개 / 검토관리 {review_count}개 / "
            f"기분류 {protected_count}개 / 실패 {failed_count}개"
        ),
    )

    window.refresh_all()
    window.stock_table.viewport().update()
    window.stock_table.repaint()

    total_count = normal_count + review_count + protected_count

    result_lines = [
        f"전체검사: {total_count}개",
        f"매매시작: {normal_count}개",
        f"검토관리: {review_count}개",
        f"기운영중: {protected_count}개",
    ]
    if failed_count:
        result_lines.append(f"처리 실패: {failed_count}개")

    window.show_auto_trade_result_dialog("안정성검사 완료", "안정성검사 결과", result_lines)



def auto_trade_open_review_required_window(window) -> None:
    """검토관리창은 루틴별이 아니라 프로그램 전체 단위로 연다."""
    dialog = GlobalReviewRequiredWindow(parent=window)
    dialog.exec_()
    window.refresh_all()

