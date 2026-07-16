# -*- coding: utf-8 -*-
"""
gui_auto_trade_timer.py

자동매매설정창의 타이머/시간정책 재판정 헬퍼.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

try:
    from routine_signal_probe import probe_selected_routine_once
except Exception:
    probe_selected_routine_once = None

try:
    from routine_signal_consumer import consume_pending_routine_signals_dry_run
except Exception:
    consume_pending_routine_signals_dry_run = None


def assigned_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """루틴 폴더 아래 실제 종목 runtime 폴더 목록을 반환한다."""
    if not routine_dir.exists() or not routine_dir.is_dir():
        return []
    result: list[Path] = []
    for child in routine_dir.iterdir():
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and not child.name.startswith("__")
            and (child / "config.json").exists()
        ):
            result.append(child)
    return result


def _read_json_dict(path: Path) -> dict:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def auto_trade_signal_probe_only_active(window) -> bool:
    routine_dir = window.current_selected_routine_dir()
    if routine_dir is None:
        return False

    try:
        from gui_auto_trade_runtime import get_stock_dirs_in_routine
        stock_dirs = get_stock_dirs_in_routine(Path(routine_dir))
    except Exception:
        stock_dirs = assigned_stock_dirs_in_routine(Path(routine_dir))

    for stock_dir in stock_dirs:
        state = _read_json_dict(Path(stock_dir) / "state.json")
        if state.get("signal_probe_only") is True:
            return True
    return False


def auto_trade_current_time_policy_minute_key(window) -> str:
    """시간정책 자동 재판정용 분 단위 키."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")



def auto_trade_current_runtime_file_signature(window) -> dict[str, int]:
    """현재 선택 루틴의 runtime 파일 변경 여부를 판단하는 간단한 스냅샷."""
    routine_dir = window.current_selected_routine_dir()
    if routine_dir is None:
        return {}

    signature: dict[str, int] = {}
    for stock_dir in assigned_stock_dirs_in_routine(routine_dir):
        for filename in ("state.json", "config.json", "orders.json"):
            path = stock_dir / filename
            try:
                signature[str(path)] = path.stat().st_mtime_ns
            except Exception:
                signature[str(path)] = -1
    return signature



def auto_trade_on_runtime_file_timer_tick(window) -> None:
    """외부 파일 수정분을 자동매매설정 표에 반영한다."""
    if not window.isVisible():
        return

    signature = window.current_runtime_file_signature()
    if signature == window._runtime_file_snapshot:
        return

    window._runtime_file_snapshot = signature
    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()
    window.load_selected_routine_stocks()
    window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
    window.update_action_buttons()



def auto_trade_on_time_policy_timer_tick(window) -> None:
    """분이 바뀐 경우에만 운영방식/시간정책을 자동 재판정한다.

    원칙:
    - 초 단위 반복 작업 금지
    - 상태 변화가 없으면 화면 갱신 금지
    - 변경 종목이 있을 때만 현재 창을 갱신
    - 긴급정지/검토종목/조기마감은 재판정 함수에서 보호
    """
    if not window.isVisible():
        return

    recovery_check = getattr(type(window), "startup_recovery_session_ready", None)
    if callable(recovery_check) and recovery_check(window, refresh=True) is not True:
        update_controls = getattr(type(window), "update_startup_recovery_controls", None)
        if callable(update_controls):
            update_controls(window)
        return

    minute_key = window.current_time_policy_minute_key()
    if minute_key == window._last_time_policy_minute_key:
        return

    window._last_time_policy_minute_key = minute_key
    result = window.recalculate_all_status_by_operation_policy(
        "시간 경과 자동 재판정",
        silent_unchanged=True,
        write_changelog_when_unchanged=False,
    )
    changed_count = int(result.get("changed", 0) or 0)
    failed_count = int(result.get("failed", 0) or 0)

    # 상태값이 바뀌지 않아도 분이 바뀌면 청산 활성/비활성 표시가 달라질 수 있다.
    # 따라서 시간 틱에서는 항상 현재 표를 다시 그린다.
    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()
    window.refresh_all()
    window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
    parent = window.parent()
    refresh_all = getattr(parent, "refresh_all", None)
    if callable(refresh_all):
        try:
            refresh_all()
        except Exception:
            pass

    # STEP 3: 루틴 evaluate() 연결 확인용 안전 프로브.
    # - 로그만 기록한다.
    # - 주문/예산/청산/state 변경 없음.
    if callable(probe_selected_routine_once):
        try:
            probe_result = probe_selected_routine_once(window, minute_key)
            logged_count = int(probe_result.get("logged", 0) or 0)
            error_count = int(probe_result.get("error", 0) or 0)
            if logged_count > 0 or error_count > 0:
                window.statusBarMessage(
                    f"루틴 신호 로그: 기록 {logged_count}개"
                    + (f" / 오류 {error_count}개" if error_count else "")
                )
            if (
                callable(consume_pending_routine_signals_dry_run)
                and auto_trade_signal_probe_only_active(window)
            ):
                try:
                    consumer_result = consume_pending_routine_signals_dry_run(
                        limit=5,
                        mark_previewed=True,
                        write_order_queue=True,
                        apply_approval=True,
                    )
                    summary = consumer_result.get("summary", {}) if isinstance(consumer_result, dict) else {}
                    checked = int(summary.get("signals_checked", 0) or 0)
                    blocked = int(summary.get("blocked", 0) or 0)
                    allowed = int(summary.get("allowed", 0) or 0)
                    errors = int(summary.get("errors", 0) or 0)
                    orders_created = int(summary.get("orders_created", 0) or 0)
                    approval_checked = int(summary.get("approval_checked", 0) or 0)
                    approved = int(summary.get("approved", 0) or 0)
                    if checked > 0 or errors > 0:
                        window.statusBarMessage(
                            f"주문후보검증: 확인 {checked} / 차단 {blocked} / 허용 {allowed} / 오류 {errors}"
                            f" / 후보 {orders_created} / 승인검사 {approval_checked} / 승인 {approved}"
                        )
                except Exception as exc:
                    window.statusBarMessage(f"주문후보검증 실패: {exc}")
        except Exception:
            pass

    if changed_count > 0 or failed_count > 0:
        window.statusBarMessage(
            f"시간정책 자동반영: 변경 {changed_count}개"
            + (f" / 실패 {failed_count}개" if failed_count else "")
        )

