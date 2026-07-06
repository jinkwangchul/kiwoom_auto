# -*- coding: utf-8 -*-
"""
gui_main_emergency_ops.py

MainWindow 긴급정지/정지해제 처리 전용 모듈.

정책:
- 긴급정지: 즉시 전체 종목 상태를 EMERGENCY_STOPPED로 전환
- 정지해제: 무결성 확인 후 정상은 STOPPED, 문제 종목은 REVIEW_REQUIRED
- 자동복귀 금지: 정지해제 후에도 매매시작 상태로 자동 복귀하지 않음
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtWidgets import QMessageBox

from gui_common_utils import safe_int_value
from gui_config_utils import default_state
from gui_order_utils import pending_order_side_quantities
from runtime_io import read_json_dict, read_orders_data
from gui_auto_trade_runtime import write_state_json
from gui_auto_trade_setting_window import (
    append_changelog,
    append_stock_log,
    now_text,
    parse_stock_folder_name,
)


def has_emergency_stopped_stock(window) -> bool:
    """MainWindow 전체 종목 중 긴급정지 상태가 하나라도 있는지 확인한다."""
    for stock_dir in window.all_runtime_stock_dirs():
        state = read_json_dict(stock_dir / "state.json")
        status = str(state.get("status", "")).strip().upper()
        if status in {"EMERGENCY_STOPPED", "EMERGENCY_STOP", "EMERGENCY"}:
            return True
    return False


def update_emergency_button_state(window) -> None:
    """긴급정지 상태 유무에 따라 버튼 문구를 갱신한다."""
    if has_emergency_stopped_stock(window):
        window.btn_emergency_stop.setText("정지해제")
    else:
        window.btn_emergency_stop.setText("긴급정지")


def emergency_review_reason_for_stock(stock_dir: Path) -> tuple[bool, str]:
    """정지해제 시 정상/검토관리 이동 기준을 판정한다."""
    state_path = stock_dir / "state.json"
    config_path = stock_dir / "config.json"
    orders_path = stock_dir / "orders.json"

    state = read_json_dict(state_path)
    config = read_json_dict(config_path)
    read_orders_data(orders_path)

    if not state_path.exists() or not isinstance(state, dict):
        return True, "state.json 이상"
    if not config_path.exists() or not isinstance(config, dict):
        return True, "config.json 이상"
    if not orders_path.exists():
        return True, "orders.json 누락"

    holding_qty = safe_int_value(state.get("holding_qty"), 0)
    if holding_qty > 0:
        return True, "긴급정지 해제 시 보유잔량 존재"

    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        return True, "긴급정지 해제 시 미체결 매수 존재"
    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        return True, "긴급정지 해제 시 미체결 매도 존재"
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        return True, "미체결 수량 확인 필요"

    return False, "긴급정지 해제 무결성 정상"


def update_runtime_stock_status(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    new_status: str,
    extra_state: dict[str, object] | None = None,
    log_suffix: str = "",
) -> bool:
    """메인창 긴급정지/정지해제 전용 state.json 상태 저장."""
    state_path = stock_dir / "state.json"
    state = read_json_dict(state_path)
    if not isinstance(state, dict):
        state = default_state()

    before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
    state["status"] = new_status
    state["updated_at"] = now_text()

    if extra_state:
        state.update(extra_state)

    if not write_state_json(stock_dir, state):
        QMessageBox.critical(
            window,
            "상태 저장 오류",
            f"{code} {name} 상태 저장 중 오류가 발생했습니다.",
        )
        append_stock_log(stock_dir, "ERROR", f"상태 저장 실패: {before_status} -> {new_status}")
        return False

    suffix_text = f" / {log_suffix}" if log_suffix else ""
    append_stock_log(stock_dir, "GUI", f"긴급정지 상태 변경: {before_status} -> {new_status}{suffix_text}")
    return True


def execute_emergency_stop(window) -> None:
    """전체 runtime 종목을 긴급정지 상태로 전환한다."""
    changed_count = 0
    for stock_dir in window.all_runtime_stock_dirs():
        code, name = parse_stock_folder_name(stock_dir.name)
        ok = update_runtime_stock_status(
            window,
            stock_dir,
            code,
            name,
            "EMERGENCY_STOPPED",
            {
                "emergency_stopped_at": now_text(),
                "emergency_reason": "USER_EMERGENCY_STOP",
                # 긴급정지는 즉시 매매 시작 플래그를 끈다.
                # 정지해제 후 자동복귀 금지 정책과 현황색 판정이 어긋나지 않도록
                # trade_enabled/buy_enabled/sell_enabled를 모두 False로 고정한다.
                "trade_enabled": False,
                "buy_enabled": False,
                "sell_enabled": False,
                # 긴급정지 진입 시 과거 마감/청산 표시 잔존 메타도 제거한다.
                # 이 값이 남아 있으면 시작 OFF 상태에서도 현황이 주황으로 보일 수 있다.
                "operation_notice": "",
                "operation_notice_reason": "",
                "operation_notice_at": "",
                "early_close_requested_at": "",
                "early_close_source": "",
                "early_close_method": "",
                "early_close_policy": {},
                "auto_close_method": "",
                "auto_close_policy": {},
                "liquidation_policy_forced": False,
                "liquidation_policy_reason": "",
            },
            "사용자 긴급정지",
        )
        if ok:
            changed_count += 1

    append_changelog("UPDATE", "state.json", f"긴급정지 실행: {changed_count}개 종목")
    window.statusBar().showMessage(f"긴급정지 실행 완료: {changed_count}개 종목")
    window.refresh_all()
    QMessageBox.information(
        window,
        "긴급정지 완료",
        "긴급정지 처리 완료\n\n"
        f"대상 종목: {changed_count}개\n"
        "신규 매수/매도: 차단\n"
        "자동판단/자동청산: 중지\n"
        "보유 종목: 자동 매도하지 않음\n\n"
        "버튼은 정지해제로 전환됩니다.",
    )


def release_emergency_stop(window) -> None:
    """긴급정지 해제 시 종목별 무결성을 확인하고 정상/검토관리로 분기한다."""
    normal_count = 0
    review_count = 0
    for stock_dir in window.all_runtime_stock_dirs():
        code, name = parse_stock_folder_name(stock_dir.name)
        routine_name = window.routine_name_for_stock_dir(stock_dir)
        has_problem, reason = emergency_review_reason_for_stock(stock_dir)
        if has_problem:
            metadata = {
                "review_required": True,
                "review_status": "PENDING",
                "review_location": "긴급정지해제",
                "review_reason": reason,
                "review_entered_at": now_text(),
                "review_checked_at": now_text(),
                "review_routine": routine_name,
                "review_detail": f"{code} {name} / {reason}",
            }
            if update_runtime_stock_status(window, stock_dir, code, name, "REVIEW_REQUIRED", metadata, reason):
                review_count += 1
        else:
            metadata = {
                "emergency_released_at": now_text(),
                "emergency_release_check": "PASSED",
                # 정지해제는 자동 매매 재개가 아니다.
                # 정상 종목도 시작 OFF 상태로 복귀해야 하므로
                # trade_started 판정에 쓰이는 플래그를 명시적으로 끈다.
                "trade_enabled": False,
                "buy_enabled": False,
                "sell_enabled": False,
                # 정지해제 성공 종목은 시작 OFF 상태로 돌아가야 하므로
                # 과거 자동/조기마감 대상 없음 알림 메타를 함께 제거한다.
                "operation_notice": "",
                "operation_notice_reason": "",
                "operation_notice_at": "",
                "early_close_requested_at": "",
                "early_close_source": "",
                "early_close_method": "",
                "early_close_policy": {},
                "auto_close_method": "",
                "auto_close_policy": {},
                "liquidation_policy_forced": False,
                "liquidation_policy_reason": "",
                "review_required": False,
                "review_status": "",
                "review_location": "",
                "review_reason": "",
                "review_detail": "",
            }
            if update_runtime_stock_status(window, stock_dir, code, name, "STOPPED", metadata, reason):
                normal_count += 1

    append_changelog(
        "UPDATE",
        "state.json",
        f"긴급정지 해제 무결성 검사: 정상 {normal_count}개 / 검토관리 {review_count}개",
    )
    window.statusBar().showMessage(
        f"정지해제 완료: 정상 {normal_count}개 / 검토관리 {review_count}개"
    )
    window.refresh_all()
    QMessageBox.information(
        window,
        "정지해제 완료",
        "무결성 검사 완료\n\n"
        f"정상 → 감시/대기: {normal_count}개\n"
        f"검토관리 이동: {review_count}개\n\n"
        "상세 내용은 검토종목 관리창에서 확인하세요.",
    )


def on_emergency_stop_clicked(window) -> None:
    """긴급정지 버튼 클릭 처리."""
    if has_emergency_stopped_stock(window):
        release_emergency_stop(window)
        return

    execute_emergency_stop(window)
