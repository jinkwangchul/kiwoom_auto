# -*- coding: utf-8 -*-
"""
gui_auto_trade_run_control.py

자동매매설정창의 매매시작/정지 처리 헬퍼.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime

from PyQt5.QtWidgets import QMessageBox

from runtime_io import read_json_dict
from gui_auto_trade_runtime import write_state_json
from gui_review_utils import review_required_for_start
from gui_config_utils import default_config
from state_policy import (
    operation_mode_display,
    real_trade_enabled,
    trade_permission_display,
    auto_trade_status_display,
    normalize_operation_mode,
    status_after_operation_mode_change,
)


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


def _refresh_signal_probe_only_window(window) -> None:
    refresh_all = getattr(window, "refresh_all", None)
    if callable(refresh_all):
        refresh_all()

    stock_table = getattr(window, "stock_table", None)
    viewport = getattr(stock_table, "viewport", None)
    if callable(viewport):
        try:
            viewport().update()
        except Exception:
            pass
    repaint = getattr(stock_table, "repaint", None)
    if callable(repaint):
        try:
            repaint()
        except Exception:
            pass


def start_signal_probe_only_for_selected_stocks(window) -> dict[str, object]:
    """Enable selected stocks for routine signal evaluation without real orders."""
    selected = window.selected_stock_infos()
    if not selected:
        status_bar = getattr(window, "statusBarMessage", None)
        if callable(status_bar):
            status_bar("신호평가 전용 전환 대상 없음")
        return {"started": [], "failed": [], "count": 0}

    started: list[str] = []
    failed: list[str] = []
    started_at = now_text()

    for stock_dir, code, name in selected:
        state = read_json_dict(stock_dir / "state.json")
        if not isinstance(state, dict):
            state = {}

        state.update(
            {
                "status": "MONITORING",
                "trade_enabled": True,
                "real_trade_enabled": False,
                "buy_enabled": False,
                "sell_enabled": False,
                "review_required": False,
                "review_status": "",
                "review_reason": "",
                "signal_probe_only": True,
                "signal_probe_started_at": started_at,
                "signal_probe_stopped_at": "",
                "updated_at": started_at,
            }
        )

        if write_state_json(stock_dir, state):
            started.append(f"{code} {name}")
        else:
            failed.append(f"{code} {name}")

    _refresh_signal_probe_only_window(window)
    status_bar = getattr(window, "statusBarMessage", None)
    if callable(status_bar):
        message = f"신호평가 전용 시작: {len(started)}개"
        if failed:
            message += f" / 실패 {len(failed)}개"
        status_bar(message)

    return {"started": started, "failed": failed, "count": len(started)}


def stop_signal_probe_only_for_selected_stocks(window) -> dict[str, object]:
    """Return selected signal-probe-only stocks to STOPPED without enabling orders."""
    selected = window.selected_stock_infos()
    if not selected:
        status_bar = getattr(window, "statusBarMessage", None)
        if callable(status_bar):
            status_bar("신호평가 전용 중지 대상 없음")
        return {"stopped": [], "failed": [], "count": 0}

    stopped: list[str] = []
    failed: list[str] = []
    stopped_at = now_text()

    for stock_dir, code, name in selected:
        state = read_json_dict(stock_dir / "state.json")
        if not isinstance(state, dict):
            state = {}

        state.update(
            {
                "status": "STOPPED",
                "trade_enabled": False,
                "real_trade_enabled": False,
                "buy_enabled": False,
                "sell_enabled": False,
                "signal_probe_only": False,
                "signal_probe_stopped_at": stopped_at,
                "updated_at": stopped_at,
            }
        )

        if write_state_json(stock_dir, state):
            stopped.append(f"{code} {name}")
        else:
            failed.append(f"{code} {name}")

    _refresh_signal_probe_only_window(window)
    status_bar = getattr(window, "statusBarMessage", None)
    if callable(status_bar):
        message = f"신호평가 전용 중지: {len(stopped)}개"
        if failed:
            message += f" / 실패 {len(failed)}개"
        status_bar(message)

    return {"stopped": stopped, "failed": failed, "count": len(stopped)}


def auto_trade_start_selected_auto_trades(window) -> None:
    selected = window.selected_stock_infos()
    routine_name = window.current_selected_routine_name()

    if not selected or not routine_name:
        QMessageBox.warning(window, "선택 오류", "감시를 시작할 종목을 1개 이상 선택하세요.")
        return

    start_targets, skipped = window.split_start_targets(selected)
    if not start_targets:
        if skipped:
            window.statusBarMessage(f"매매시작 대상 없음: 이미 감시 중/보호 상태 {len(skipped)}개 제외")
        else:
            window.statusBarMessage("매매시작 대상 없음")
        return

    completed: list[str] = []
    review_required: list[str] = []

    for stock_dir, code, name in start_targets:
        review_item = window.pre_start_review_check(routine_name, stock_dir, code, name)

        if review_required_for_start(review_item):
            if window.mark_review_required(stock_dir, code, name, review_item, source="매매시작"):
                review_required.append(f"{code} {name}")
            continue

        config = read_json_dict(stock_dir / "config.json")
        if not config:
            config = default_config()

        operation_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
        start_status = status_after_operation_mode_change(operation_mode, config)
        status_display = auto_trade_status_display(start_status)
        mode_display = operation_mode_display(operation_mode)
        trade_permission_text, _, _ = trade_permission_display(config)

        metadata = {
            "review_required": False,
            "review_status": "",
            "review_reason": "",
            "resumed_at": now_text(),
            "ignore_signals_before": now_text(),
            # operation_mode는 config.json만 원본으로 사용한다.
            # state.json에는 저장하지 않는다.
            "real_trade_enabled": real_trade_enabled(config),
            "trade_enabled": True,
            "trade_started_at": now_text(),
            "startup_reset_reason": "",
            "startup_reset_cleared_at": now_text(),
            "operation_notice": "",
            "operation_notice_reason": "",
            "operation_notice_at": "",
            "start_policy_status": start_status,
            "start_policy_checked_at": now_text(),
        }
        result, _, applied_status = window.recalculate_stock_status_by_operation_policy(
            stock_dir,
            code,
            name,
            "매매시작",
            metadata,
        )
        if result in ("changed", "unchanged"):
            completed.append(f"{code} {name}({mode_display}/{trade_permission_text}/{auto_trade_status_display(applied_status)})")

    if completed or review_required:
        changelog_parts: list[str] = []
        if completed:
            changelog_parts.append(f"시작: {' / '.join(completed)}")
        if review_required:
            changelog_parts.append(f"검토종목: {' / '.join(review_required)}")
        if skipped:
            changelog_parts.append(f"제외: {' / '.join(skipped)}")

        append_changelog(
            "UPDATE",
            "state.json",
            f"매매시작 전 안정성검사 및 operation_mode 반영: {routine_name} -> {' | '.join(changelog_parts)}",
        )

    window.refresh_all()
    window.stock_table.viewport().update()
    window.stock_table.repaint()

    result_lines = [
        f"매매시작: {len(completed)}개",
        f"기운영중: {len(skipped)}개",
        f"검토관리: {len(review_required)}개",
    ]

    window.show_auto_trade_result_dialog("안정성검사 완료", "안정성검사 결과", result_lines)
    if review_required:
        window.open_review_required_window()



def auto_trade_stop_selected_auto_trades(window) -> None:
    selected = window.selected_stock_infos()
    routine_name = window.current_selected_routine_name()

    if not selected or not routine_name:
        QMessageBox.warning(window, "선택 오류", "감시를 종료할 종목을 1개 이상 선택하세요.")
        return

    stop_targets, skipped = window.split_stop_targets(selected)
    if not stop_targets:
        window.statusBarMessage("강제종료 대상 없음: 이미 중지된 종목")
        return

    if not window.confirm_stop_targets_once(stop_targets):
        window.statusBarMessage("강제종료 취소")
        return

    completed: list[str] = []
    review_moved: list[str] = []

    for stock_dir, code, name in stop_targets:
        risk_parts = window.stop_risk_parts(stock_dir)

        if risk_parts:
            reason_text = "강제종료 요청: " + " + ".join(risk_parts)
            metadata = {
                "review_required": True,
                "review_status": "PENDING",
                "review_location": "강제종료",
                "review_reason": reason_text,
                "review_entered_at": now_text(),
                "review_checked_at": now_text(),
                "review_routine": routine_name,
                "review_detail": f"{code} {name} / {reason_text}",
                "trade_enabled": False,
                "trade_stopped_at": now_text(),
            }
            if window.update_stock_status(stock_dir, code, name, "REVIEW_REQUIRED", metadata, reason_text):
                review_moved.append(f"{code} {name}")
            continue

        metadata = {
            "review_required": False,
            "review_status": "",
            "review_location": "",
            "review_reason": "",
            "review_detail": "",
            "trade_enabled": False,
            "buy_enabled": False,
            "sell_enabled": False,
            "trade_stopped_at": now_text(),
            "early_close_requested_at": "",
            "early_close_source": "",
            "early_close_method": "",
            "early_close_policy": {},
            "liquidation_policy_forced": False,
            "liquidation_policy_reason": "",
            "operation_notice": "",
            "operation_notice_reason": "",
            "operation_notice_at": "",
        }
        if window.update_stock_status(stock_dir, code, name, "STOPPED", metadata, "강제종료"):
            completed.append(f"{code} {name}")

    if completed or review_moved:
        changelog_parts: list[str] = []
        if completed:
            changelog_parts.append(f"강제종료: {' / '.join(completed)}")
        if review_moved:
            changelog_parts.append(f"별도 확인: {' / '.join(review_moved)}")
        if skipped:
            changelog_parts.append(f"제외: {' / '.join(skipped)}")

        append_changelog(
            "UPDATE",
            "state.json",
            f"강제종료 상태 변경: {routine_name} -> {' | '.join(changelog_parts)}",
        )

    window.refresh_all()
    window.stock_table.viewport().update()
    window.stock_table.repaint()

    result_lines = [f"강제종료 처리 완료: 중지 {len(completed)}개"]
    if review_moved:
        result_lines.append(f"검토관리 {len(review_moved)}개")
    if skipped:
        result_lines.append(f"제외 {len(skipped)}개")
    window.statusBarMessage(" / ".join(result_lines))

    if review_moved:
        window.open_review_required_window()
