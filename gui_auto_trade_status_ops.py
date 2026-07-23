# -*- coding: utf-8 -*-
"""
gui_auto_trade_status_ops.py

자동매매설정창의 상태 재판정/운영방식 변경 처리 헬퍼.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import QMessageBox

from gui_config_utils import default_config, default_state
from gui_review_utils import review_reason_summary
from gui_schedule_utils import (
    schedule_change_log_text,
    schedule_status_suffix,
)
from runtime_io import read_json_dict
from gui_auto_trade_runtime import write_state_json
from state_policy import (
    auto_trade_status_display,
    normalize_operation_mode,
    normalized_hhmmss_or_empty,
    operation_mode_check_text,
    operation_mode_display,
    operation_mode_recalculation_target_status,
    scheduled_status_for_now,
    start_status_by_operation_mode,
    status_after_operation_mode_change,
    validate_buy_time_range,
)
from gui_auto_trade_policy import (
    auto_trade_setting_should_preserve_raw_status,
    auto_trade_setting_trade_started,
)


PROJECT_ROOT = Path(__file__).resolve().parent
ROUTINES_DIR = PROJECT_ROOT / "routines"
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


def append_stock_log(stock_dir: Path, event_type: str, message: str) -> Path | None:
    try:
        logs_dir = stock_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{datetime.now().strftime('%Y%m%d')}.log"
        line = f"[{now_text()}] [{event_type}] {message}"
        with log_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        return log_path
    except Exception:
        return None



def get_routine_dirs() -> list[Path]:
    """자동매매 루틴 폴더 목록을 반환한다."""
    if not ROUTINES_DIR.exists() or not ROUTINES_DIR.is_dir():
        return []
    return [
        path
        for path in sorted(ROUTINES_DIR.iterdir(), key=lambda item: item.name)
        if path.is_dir() and not path.name.startswith(".") and not path.name.startswith("__")
    ]


def get_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
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


def parse_stock_folder_name(folder_name: str) -> tuple[str, str]:
    """종목 폴더명에서 코드/종목명을 추출한다."""
    text = str(folder_name).strip()
    if "_" in text:
        code, name = text.split("_", 1)
        return code.strip(), name.strip()
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return text.strip(), ""



def auto_trade_resume_status_after_pause(window, state: dict[str, object]) -> tuple[str, dict[str, object], str]:
    """
    일시중지 후 재시작 정책.

    확정 정책:
    - 일시중지 기간 동안 매수/매도 신호가 1건이라도 확인되면 REVIEW_REQUIRED.
    - 매수/매도 신호가 모두 0건으로 확인된 경우에만 RUNNING 재시작 허용.
    - 신호 발생 여부를 아직 확인할 수 없는 경우도 안전하게 REVIEW_REQUIRED.

    현재 단계에서는 실제 루틴 신호 재계산 루프가 아직 연결되지 않았으므로,
    향후 신호 검증 모듈이 state.json에 기록할 아래 필드를 기준으로 판정한다.
    - pause_signal_check_status: CHECKED / UNCHECKED / FAILED
    - missed_buy_signal_count
    - missed_sell_signal_count
    """
    missed_buy = window.int_state_value(state, "missed_buy_signal_count")
    missed_sell = window.int_state_value(state, "missed_sell_signal_count")
    check_status = str(state.get("pause_signal_check_status", "UNCHECKED")).strip().upper()

    metadata: dict[str, object] = {
        "review_checked_at": now_text(),
        "missed_buy_signal_count": missed_buy,
        "missed_sell_signal_count": missed_sell,
    }

    if missed_buy > 0 or missed_sell > 0:
        metadata.update(
            {
                "review_required": True,
                "review_reason": "SIGNAL_OCCURRED_DURING_PAUSE",
            }
        )
        return "REVIEW_REQUIRED", metadata, "일시중지 중 매수/매도 신호 발생"

    if check_status == "CHECKED":
        metadata.update(
            {
                "review_required": False,
                "review_reason": "",
                "resumed_at": now_text(),
                "ignore_signals_before": now_text(),
            }
        )
        return "RUNNING", metadata, "일시중지 중 매수/매도 신호 없음"

    metadata.update(
        {
            "review_required": True,
            "review_reason": "PAUSE_SIGNAL_CHECK_UNAVAILABLE",
        }
    )
    return "REVIEW_REQUIRED", metadata, "일시중지 중 신호 발생 여부 확인 필요"



def auto_trade_update_stock_status(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    new_status: str,
    extra_state: dict[str, object] | None = None,
    log_suffix: str = "",
) -> bool:
    state_path = stock_dir / "state.json"
    state = read_json_dict(state_path)
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

    suppress_normal_log = (
        isinstance(extra_state, dict)
        and str(extra_state.get("operation_notice", "")).strip().upper()
        in {"NO_CLOSE_TARGET", "AUTO_CLOSE_NO_TARGET", "EARLY_CLOSE_NO_TARGET"}
        and not log_suffix
    )
    if not suppress_normal_log:
        suffix_text = f" / {log_suffix}" if log_suffix else ""
        append_stock_log(stock_dir, "GUI", f"자동매매 상태 변경: {before_status} -> {new_status}{suffix_text}")
    return True



def auto_trade_operation_policy_protected_status(window, status: object) -> bool:
    """운영방식/시간정책 자동 재판정에서 건드리면 안 되는 보호 상태.

    단순 status 값만으로 조기마감을 보호하지 않는다.
    조기마감 보호 여부는 state 메타값까지 함께 보는
    auto_trade_setting_should_preserve_raw_status()에서 판단한다.
    """
    current = str(status or "STOPPED").strip().upper() or "STOPPED"
    return current in {
        "EMERGENCY_STOPPED",
        "EMERGENCY_STOP",
        "EMERGENCY",
        "REVIEW_REQUIRED",
        "REVIEW",
        "FORCE_CLOSE",
        "FORCE_LIQUIDATION",
    }



def auto_trade_recalculate_stock_status_by_operation_policy(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    reason: str,
    extra_state: dict[str, object] | None = None,
    silent_unchanged: bool = False,
) -> tuple[str, str, str]:
    """운영방식/현재시간 기준으로 상태를 중앙 재판정한다.

    반환값: (result, before_status, after_status)
    - changed: 상태 변경됨
    - unchanged: 재판정했지만 상태 동일
    - protected: 긴급정지/검토종목/조기마감 등 보호상태라 미변경
    - failed: 저장 실패
    """
    state = read_json_dict(stock_dir / "state.json")
    before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"

    if bool(state.get("review_required", False)):
        if not silent_unchanged:
            append_stock_log(
                stock_dir,
                "GUI",
                f"운영정책 재판정 보호: 검토관리 우선 / {reason}",
            )
        return "protected", before_status, before_status

    if bool(state.get("signal_probe_only", False)):
        probe_metadata = {
            "trade_enabled": True,
            "real_trade_enabled": False,
            "buy_enabled": False,
            "sell_enabled": False,
            "signal_probe_only": True,
            "operation_policy_recalculated_at": now_text(),
            "operation_policy_reason": reason,
            "operation_policy_mode": "SIGNAL_PROBE_ONLY",
        }
        needs_restore = before_status != "MONITORING" or any(
            state.get(key) != value for key, value in probe_metadata.items()
            if key in {"trade_enabled", "real_trade_enabled", "buy_enabled", "sell_enabled", "signal_probe_only"}
        )
        if needs_restore:
            if window.update_stock_status(
                stock_dir,
                code,
                name,
                "MONITORING",
                probe_metadata,
                "신호평가 전용 상태 보호",
            ):
                return "protected", before_status, "MONITORING"
            return "failed", before_status, "MONITORING"

        if not silent_unchanged:
            append_stock_log(
                stock_dir,
                "GUI",
                f"운영정책 재판정 보호: 신호평가 전용 상태 유지 / {reason}",
            )
        return "protected", before_status, before_status

    if auto_trade_setting_should_preserve_raw_status(state, before_status):
        if not silent_unchanged:
            append_stock_log(
                stock_dir,
                "GUI",
                f"운영정책 재판정 보호상태 유지: {auto_trade_status_display(before_status)} / {reason}",
            )
        return "protected", before_status, before_status

    # 재시작/수동중지 상태에서는 시간 타이머가 상태를 자동으로 다시 켜면 안 된다.
    # 매매시작 버튼이 trade_enabled=True 메타를 전달한 경우에만 시간정책 재판정 진입을 허용한다.
    start_requested = bool(extra_state and extra_state.get("trade_enabled") is True)
    if not start_requested and not auto_trade_setting_trade_started(state):
        if not silent_unchanged:
            append_stock_log(
                stock_dir,
                "GUI",
                f"운영정책 재판정 제외: 매매시작 전/재시작 중지 상태 / {reason}",
            )
        return "unchanged", before_status, before_status

    config = read_json_dict(stock_dir / "config.json")
    if not config:
        config = default_config()

    mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    new_status = status_after_operation_mode_change(mode, config)

    metadata = {
        "operation_policy_recalculated_at": now_text(),
        "operation_policy_reason": reason,
        "operation_policy_mode": mode,
    }
    if extra_state:
        metadata.update(extra_state)

    if new_status == before_status:
        # 상태가 같아도 매매시작/강제종료 계열의 메타값은 반드시 저장한다.
        # 예: 감시/대기 -> 감시/대기 상태유지여도 trade_enabled=True가 저장되어야
        # 현황 컬럼이 즉시 켜지고 이후 시간정책 자동판정 대상이 된다.
        if extra_state:
            log_suffix = (
                f"운영정책 재판정 상태유지/메타갱신: "
                f"{operation_mode_display(mode)} / {auto_trade_status_display(before_status)} / {reason}"
            )
            if window.update_stock_status(stock_dir, code, name, new_status, metadata, log_suffix):
                return "unchanged", before_status, new_status
            return "failed", before_status, new_status

        if not silent_unchanged:
            append_stock_log(
                stock_dir,
                "GUI",
                f"운영정책 재판정 상태유지: {auto_trade_status_display(before_status)} / {operation_mode_display(mode)} / {reason}",
            )
        return "unchanged", before_status, new_status

    log_suffix = (
        f"운영정책 재판정: {operation_mode_display(mode)} / "
        f"{auto_trade_status_display(before_status)} -> {auto_trade_status_display(new_status)} / {reason}"
    )
    if window.update_stock_status(stock_dir, code, name, new_status, metadata, log_suffix):
        return "changed", before_status, new_status
    return "failed", before_status, new_status



def auto_trade_recalculate_all_status_by_operation_policy(
    window,
    reason: str,
    silent_unchanged: bool = False,
    write_changelog_when_unchanged: bool = True,
) -> dict[str, int]:
    """전체 루틴 전체 종목을 운영방식/현재시간 기준으로 재판정한다."""
    result = {"changed": 0, "unchanged": 0, "protected": 0, "failed": 0}
    for routine_dir in get_routine_dirs():
        for stock_dir in get_stock_dirs_in_routine(routine_dir):
            code, name = parse_stock_folder_name(stock_dir.name)
            status, _, _ = window.recalculate_stock_status_by_operation_policy(
                stock_dir,
                code,
                name,
                reason,
                silent_unchanged=silent_unchanged,
            )
            if status not in result:
                result[status] = 0
            result[status] += 1
    if write_changelog_when_unchanged or result.get("changed", 0) or result.get("failed", 0):
        append_changelog(
            "UPDATE",
            "state.json",
            f"전체 운영정책 재판정: {reason} / 변경 {result.get('changed', 0)}개 / 유지 {result.get('unchanged', 0)}개 / 보호 {result.get('protected', 0)}개 / 실패 {result.get('failed', 0)}개",
        )
    return result



def auto_trade_update_stock_operation_mode(window, stock_dir: Path, code: str, name: str, operation_mode: str, config_updates: dict[str, object] | None = None) -> bool:
    mode = normalize_operation_mode(operation_mode)
    config_path = stock_dir / "config.json"
    config = read_json_dict(config_path)
    if not config:
        config = default_config()

    before_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    config["operation_mode"] = mode
    if config_updates:
        config.update(config_updates)

        start_time = normalized_hhmmss_or_empty(
            config.get("start_time", config.get("trade_start_time", ""))
        )
        end_buy_time = normalized_hhmmss_or_empty(
            config.get("end_buy_time", config.get("buy_end_time", ""))
        )
        if start_time and end_buy_time:
            config["start_time"] = start_time
            config["trade_start_time"] = start_time
            config["end_buy_time"] = end_buy_time
            config["buy_end_time"] = end_buy_time

    config["operation_mode_updated_at"] = now_text()

    try:
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        QMessageBox.critical(
            window,
            "운영방식 저장 오류",
            f"{code} {name} 운영방식 저장 중 오류가 발생했습니다.\n\n{exc}",
        )
        append_stock_log(stock_dir, "ERROR", f"운영방식 저장 실패: {operation_mode_display(before_mode)} -> {operation_mode_display(mode)} / {exc}")
        return False

    saved_config = read_json_dict(config_path)
    if saved_config != config:
        QMessageBox.critical(
            window,
            "운영방식 저장 오류",
            f"{code} {name} 운영방식 저장 결과를 확인하지 못했습니다.",
        )
        append_stock_log(
            stock_dir,
            "ERROR",
            f"운영방식 저장 read-back 실패: {operation_mode_display(before_mode)} -> {operation_mode_display(mode)}",
        )
        return False

    append_stock_log(stock_dir, "GUI", f"운영방식 변경: {operation_mode_display(before_mode)} -> {operation_mode_display(mode)}")
    return True



def auto_trade_set_selected_schedule_operation_mode(window) -> None:
    """
    하위 호환용: 선택 종목 개별 시간설정으로 연결한다.
    """
    window.set_selected_individual_schedule_time()



def auto_trade_set_selected_operation_mode(window, operation_mode: str, config_updates: dict[str, object] | None = None) -> None:
    selected = window.selected_stock_infos()
    routine_name = window.current_selected_routine_name()

    if not selected or not routine_name:
        QMessageBox.warning(window, "선택 오류", "운영방식을 변경할 종목을 1개 이상 선택하세요.")
        return

    mode = normalize_operation_mode(operation_mode)
    display_mode = operation_mode_display(mode)
    completed: list[str] = []
    failed: list[str] = []
    status_changed: list[str] = []
    status_failed: list[str] = []
    protected: list[str] = []

    for stock_dir, code, name in selected:
        if not window.update_stock_operation_mode(stock_dir, code, name, mode, config_updates):
            failed.append(f"{code} {name}")
            continue

        completed.append(f"{code} {name}")

        result, before_status, new_status = window.recalculate_stock_status_by_operation_policy(
            stock_dir,
            code,
            name,
            "운영방식/시간 설정 변경",
            {"operation_mode_status_applied_at": now_text()},
        )
        if result == "changed":
            status_changed.append(f"{code} {name}({auto_trade_status_display(new_status)})")
        elif result == "failed":
            status_failed.append(f"{code} {name}")
        elif result == "protected":
            protected.append(f"{code} {name}({auto_trade_status_display(before_status)})")

    if completed:
        changelog_parts = [f"대상: {' / '.join(completed)}"]
        schedule_log_text = schedule_change_log_text(config_updates)
        if schedule_log_text:
            changelog_parts.append(schedule_log_text)
        if status_changed:
            changelog_parts.append(f"상태재판정: {' / '.join(status_changed)}")
        if status_failed:
            changelog_parts.append(f"상태재판정실패: {' / '.join(status_failed)}")
        if protected:
            changelog_parts.append(f"보호상태유지: {' / '.join(protected)}")

        append_changelog(
            "UPDATE",
            "config.json/state.json",
            f"종목별 운영방식 변경: {routine_name} -> {display_mode}: {' | '.join(changelog_parts)}",
        )

    window.refresh_all()
    parent = window.parent()
    refresh_parent = getattr(parent, "refresh_all", None)
    if callable(refresh_parent):
        refresh_parent()

    if not completed:
        window.statusBarMessage(f"운영방식 변경 실패: {display_mode} / 실패 {len(failed)}개")
        return

    status_text = f"운영방식 변경 완료: {display_mode} {len(completed)}개"
    if failed:
        status_text += f" / 실패 {len(failed)}개"
    schedule_suffix = schedule_status_suffix(config_updates)
    if schedule_suffix:
        status_text += schedule_suffix
    if status_changed:
        status_text += f" / 상태재판정 {len(status_changed)}개"
    if status_failed:
        status_text += f" / 상태재판정 실패 {len(status_failed)}개"
    if protected:
        status_text += f" / 보호상태유지 {len(protected)}개"
    window.statusBarMessage(status_text)



def auto_trade_set_selected_stocks_buy_end(window) -> None:
    """선택 종목을 SELL_ONLY 상태로 전환한다. 화면 표시는 '감시/매도'로 한다."""
    selected = window.selected_stock_infos()
    routine_name = window.current_selected_routine_name()

    if not selected or not routine_name:
        QMessageBox.warning(window, "선택 오류", "매수종료할 종목을 1개 이상 선택하세요.")
        return

    targets: list[tuple[Path, str, str]] = []
    skipped: list[str] = []
    allowed_statuses = {"RUNNING", "MONITORING"}

    for stock_dir, code, name in selected:
        state = read_json_dict(stock_dir / "state.json")
        status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
        if status in allowed_statuses:
            targets.append((stock_dir, code, name))
        else:
            skipped.append(f"{code} {name}({auto_trade_status_display(status)})")

    if not targets:
        message = "매수종료 전환 대상 없음"
        if skipped:
            message += f": 제외 {len(skipped)}개"
        window.statusBarMessage(message)
        return

    preview = "\n".join(f"- {code} {name}" for _, code, name in targets[:8])
    if len(targets) > 8:
        preview += f"\n- 외 {len(targets) - 8}개"

    box = QMessageBox(window)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("매수종료 확인")
    box.setText(
        "선택 종목을 매수종료 상태로 전환합니다.\n\n"
        "신규매수는 중단되고 보유분 매도 조건만 계속 관리됩니다.\n\n"
        f"대상:\n{preview}\n\n"
        "계속하시겠습니까?"
    )
    proceed_button = box.addButton("진행", QMessageBox.AcceptRole)
    box.addButton("취소", QMessageBox.RejectRole)
    box.setDefaultButton(proceed_button)
    box.exec_()
    if box.clickedButton() != proceed_button:
        window.statusBarMessage("매수종료 전환 취소")
        return

    completed: list[str] = []
    for stock_dir, code, name in targets:
        metadata = {
            "buy_end_requested_at": now_text(),
            "buy_end_reason": "USER_CONTEXT_MENU",
        }
        if window.update_stock_status(stock_dir, code, name, "SELL_ONLY", metadata, "상태 칼럼 우클릭 매수종료"):
            completed.append(f"{code} {name}")

    if completed:
        changelog_message = f"선택종목 매수종료 전환: {routine_name} -> {' / '.join(completed)}"
        if skipped:
            changelog_message += f" / 제외: {' / '.join(skipped)}"
        append_changelog("UPDATE", "state.json", changelog_message)

    window.refresh_all()
    window.stock_table.viewport().update()
    window.stock_table.repaint()

    message = f"매수종료 전환 완료: {len(completed)}개"
    if skipped:
        message += f" / 제외 {len(skipped)}개"
    window.statusBarMessage(message)
