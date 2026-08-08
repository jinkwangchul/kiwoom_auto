# -*- coding: utf-8 -*-
"""
gui_auto_trade_ats_ops.py

자동매매설정창의 수동운영 ATS 설정 처리 헬퍼.

주의:
- 조기마감/자동마감/청산 정책은 다루지 않는다.
- AutoTradeSettingWindow 본체를 직접 import하지 않고 window 객체를 인자로 받아 동작한다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QDialog, QMessageBox
from gui_operation_ui_context import operation_dialog_parent

from gui_auto_trade_runtime import now_text
from gui_ats_utils import (
    ManualAtsSettingsDialog,
    manual_ats_session_labels,
    manual_ats_visible_session_keys,
)
from gui_auto_trade_policy import auto_trade_setting_liquidation_completed_today
from manual_ats_liquidation_service import (
    build_manual_ats_liquidation_preview,
    commit_manual_ats_liquidation_preview,
)
from operation_command_service import OperationCommandService
from runtime_io import read_json_dict
from manual_ats_runtime import (
    manual_ats_runtime_selected_keys,
    write_manual_ats_runtime_selection,
)
from state_policy import normalize_operation_mode


PROJECT_ROOT = Path(__file__).resolve().parent


def append_stock_log(stock_dir: Path, event_type: str, message: str) -> Path | None:
    """종목별 GUI 조작 로그를 기록한다. 실패해도 GUI 흐름은 막지 않는다."""
    try:
        logs_dir = stock_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{now_text()[:10].replace('-', '')}.log"
        line = f"[{now_text()}] [{event_type}] {message}"
        with log_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        return log_path
    except Exception:
        return None


def auto_trade_selected_manual_ats_state(
    window,
    selected: list[tuple[Path, str, str]] | None = None,
) -> dict[str, bool]:
    """선택 수동운영 종목들의 ATS 체크 상태를 메뉴 표시용으로 합산한다."""
    selected = selected if selected is not None else window.selected_stock_infos()
    result = {"extra1": False, "extra2": False, "extra3": False}
    for stock_dir, _, _ in selected:
        state = read_json_dict(stock_dir / "state.json")
        sessions = manual_ats_runtime_selected_keys(state)
        for key in result:
            result[key] = result[key] or key in sessions
    return result


def _manual_ats_targets(
    selected: list[tuple[Path, str, str]],
) -> tuple[list[tuple[Path, str, str]], list[tuple[Path, str, str]]]:
    eligible: list[tuple[Path, str, str]] = []
    excluded: list[tuple[Path, str, str]] = []
    for target in selected:
        stock_dir, _code, _name = target
        config = read_json_dict(stock_dir / "config.json")
        if normalize_operation_mode(config.get("operation_mode", "SCHEDULED")) == "CONTINUOUS":
            eligible.append(target)
        else:
            excluded.append(target)
    return eligible, excluded


def auto_trade_save_manual_ats_state_for_targets(
    window,
    selected: list[tuple[Path, str, str]],
    ats_state: dict[str, bool],
    editable_keys: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Apply ATS state to a fixed target snapshot through the existing writer."""
    targets = list(selected)
    dialog_parent = operation_dialog_parent(window)
    result: dict[str, object] = {
        "requested": len(targets),
        "succeeded": 0,
        "failed": 0,
        "excluded": 0,
        "results": [],
    }
    target_results = result["results"]
    assert isinstance(target_results, list)
    if not targets:
        QMessageBox.warning(dialog_parent, "선택 오류", "ATS설정을 변경할 수동운영 종목을 선택하세요.")
        return result

    eligible_targets, excluded_targets = _manual_ats_targets(targets)
    if excluded_targets:
        eligible_paths = {str(stock_dir) for stock_dir, _code, _name in eligible_targets}
        for stock_dir, code, name in targets:
            is_eligible = str(stock_dir) in eligible_paths
            target_results.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "stock_dir": str(stock_dir),
                    "success": None,
                    "status": (
                        "BLOCKED_MIXED_OPERATION_MODE"
                        if is_eligible
                        else "EXCLUDED"
                    ),
                    "reason": (
                        "선택 대상에 시간운영 종목이 포함되어 ATS 적용을 중단했습니다."
                        if is_eligible
                        else "수동운영 종목이 아닙니다."
                    ),
                }
            )
        result["excluded"] = len(excluded_targets)
        return result

    all_keys = ("extra1", "extra2", "extra3")
    editable = set(all_keys if editable_keys is None else editable_keys)

    for stock_dir, code, name in targets:
        config = read_json_dict(stock_dir / "config.json")
        current_state = read_json_dict(stock_dir / "state.json")
        current_keys = set(manual_ats_runtime_selected_keys(current_state))
        normalized = {
            key: bool(ats_state.get(key, False)) if key in editable else key in current_keys
            for key in all_keys
        }

        if not write_manual_ats_runtime_selection(stock_dir, normalized):
            target_results.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "stock_dir": str(stock_dir),
                    "success": False,
                    "reason": "현재 운영세션 ATS 상태 저장 또는 read-back에 실패했습니다.",
                }
            )
            result["failed"] = int(result["failed"]) + 1
            continue

        label_map = manual_ats_session_labels()
        enabled_labels = [
            str(label_map.get(key, fallback_label))
            for key, fallback_label in [
                ("extra1", "추가1"),
                ("extra2", "추가2"),
                ("extra3", "추가3"),
            ]
            if normalized.get(key, False)
        ]
        label_text = ", ".join(enabled_labels) if enabled_labels else "없음"
        append_stock_log(stock_dir, "GUI", f"현재 운영세션 ATS 적용: {label_text}")
        target_results.append(
            {
                "stock_code": code,
                "stock_name": name,
                "stock_dir": str(stock_dir),
                "success": True,
                "reason": "",
            }
        )
        result["succeeded"] = int(result["succeeded"]) + 1

    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()
    window.load_selected_routine_stocks()
    window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
    window._runtime_file_snapshot = window.current_runtime_file_signature()
    window.update_action_buttons()
    if int(result["succeeded"]):
        parent = window.parent()
        parent_refresh = getattr(parent, "refresh_all", None)
        if callable(parent_refresh):
            parent_refresh()
    return result


def auto_trade_save_selected_manual_ats_state(
    window,
    ats_state: dict[str, bool],
    selected: list[tuple[Path, str, str]] | None = None,
    editable_keys: tuple[str, ...] | None = None,
) -> int:
    """Apply ATS state to the selected snapshot and keep the legacy count result."""
    targets = list(selected) if selected is not None else list(window.selected_stock_infos())
    result = auto_trade_save_manual_ats_state_for_targets(
        window,
        targets,
        ats_state,
        editable_keys=editable_keys,
    )
    return int(result["succeeded"])


def auto_trade_open_selected_manual_ats_settings_dialog(window) -> None:
    """수동운영 ATS설정을 체크박스 창으로 연다."""
    selected = list(window.selected_stock_infos())
    dialog_parent = operation_dialog_parent(window)
    if not selected:
        QMessageBox.warning(dialog_parent, "선택 오류", "ATS설정을 변경할 수동운영 종목을 선택하세요.")
        return

    manual_targets, excluded_targets = _manual_ats_targets(selected)
    if not manual_targets or excluded_targets:
        return

    blocked_after_liquidation: list[str] = []
    for stock_dir, code, name in manual_targets:
        state = read_json_dict(stock_dir / "state.json")
        if auto_trade_setting_liquidation_completed_today(state):
            blocked_after_liquidation.append(f"{code} {name}")
    if blocked_after_liquidation:
        QMessageBox.warning(
            dialog_parent,
            "ATS설정 불가",
            "금일 청산 완료 종목은 시간외/ATS 거래를 다시 열 수 없습니다.\n\n"
            + "\n".join(blocked_after_liquidation[:10]),
        )
        return

    visible_keys = manual_ats_visible_session_keys()
    dialog = ManualAtsSettingsDialog(
        window.selected_manual_ats_state(manual_targets),
        manual_ats_session_labels(),
        dialog_parent,
        visible_keys=visible_keys,
    )
    if dialog.exec_() != QDialog.Accepted:
        return

    if dialog.requested_sell_method:
        selected_visible_keys = dialog.selected_visible_keys()
        sell_args = (
            dialog.requested_sell_method,
            dialog.values(),
            manual_targets,
        )
        if visible_keys == ("extra1", "extra2", "extra3"):
            window.execute_selected_manual_ats_liquidation(*sell_args)
        else:
            window.execute_selected_manual_ats_liquidation(
                *sell_args,
                visible_keys,
                selected_visible_keys,
            )
        return

    new_state = dialog.values()
    if visible_keys == ("extra1", "extra2", "extra3"):
        changed_count = window.save_selected_manual_ats_state(new_state, manual_targets)
    else:
        changed_count = window.save_selected_manual_ats_state(
            new_state,
            manual_targets,
            visible_keys,
        )
    failed_count = len(manual_targets) - changed_count
    enabled_count = sum(1 for key in ["extra1", "extra2", "extra3"] if new_state.get(key, False))
    if failed_count:
        title = "ATS설정 일부 실패" if changed_count else "ATS설정 적용 오류"
        QMessageBox.warning(
            dialog_parent,
            title,
            f"성공 {changed_count}개 / 실패 {failed_count}개",
        )
    if changed_count:
        window.statusBarMessage(
            f"ATS설정 적용 완료: 활성 {enabled_count}개 / "
            f"성공 {changed_count}개 / 실패 {failed_count}개"
        )


def auto_trade_set_selected_manual_ats_flag(window, flag_key: str, enabled: bool, label: str) -> None:
    """기존 우클릭 체크 액션 호환용. 현재는 ATS설정 창을 기본 UI로 사용한다."""
    current = window.selected_manual_ats_state()
    current[flag_key] = bool(enabled)
    changed_count = window.save_selected_manual_ats_state(current)
    window.statusBarMessage(f"ATS설정 변경 완료: {label} {'ON' if enabled else 'OFF'} / {changed_count}개")


def _manual_ats_result_status(execution_result: dict[str, object]) -> str:
    send_result = execution_result.get("send_order_result")
    send_result = send_result if isinstance(send_result, dict) else {}
    if send_result.get("send_call_accepted") is True:
        return "SEND_CALL_ACCEPTED"
    if send_result.get("send_call_rejected") is True:
        return "SEND_CALL_REJECTED"
    if send_result.get("send_uncertain") is True or send_result.get("callable_executed") is True:
        return "SEND_CALL_UNCERTAIN"
    return "ORDER_BLOCKED"


def auto_trade_execute_selected_manual_ats_liquidation(
    window,
    method: str,
    ats_state: dict[str, bool],
    selected: list[tuple[Path, str, str]] | None = None,
    editable_keys: tuple[str, ...] | None = None,
    selected_sessions: tuple[str, ...] | None = None,
) -> None:
    """현재 선택 ATS 구간에서 수동운영 종목의 일회성 청산을 실행한다."""
    selected = list(selected) if selected is not None else list(window.selected_stock_infos())
    dialog_parent = operation_dialog_parent(window)
    if not selected:
        QMessageBox.warning(dialog_parent, "선택 오류", "매도할 수동운영 종목을 선택하세요.")
        return

    selected_sessions = list(selected_sessions) if selected_sessions is not None else [
        key for key in ("extra1", "extra2", "extra3") if bool(ats_state.get(key, False))
    ]
    if not selected_sessions:
        QMessageBox.warning(dialog_parent, "ATS 매도 불가", "현재 운영세션에 적용할 ATS 구간을 선택하세요.")
        return

    applied_count = window.save_selected_manual_ats_state(
        ats_state,
        selected,
        editable_keys,
    )
    if applied_count != len(selected):
        QMessageBox.warning(
            dialog_parent,
            "ATS 매도 불가",
            "선택한 모든 수동운영 종목에 ATS 상태를 적용하지 못해 청산 요청을 중단했습니다.",
        )
        return

    previews: list[dict[str, object]] = []
    blocked: list[str] = []
    for stock_dir, code, name in selected:
        preview = build_manual_ats_liquidation_preview(
            stock_dir,
            code,
            name,
            selected_sessions,
            method,
        )
        previews.append(preview)
        if preview.get("ok") is not True:
            reasons = ", ".join(str(value) for value in preview.get("blocked_reasons", []) if value)
            blocked.append(f"{code} {name}: {reasons or '청산 준비 실패'}")
    if blocked:
        QMessageBox.warning(
            dialog_parent,
            f"ATS {method}매도 불가",
            "선택 종목 중 ATS 청산 안전조건을 충족하지 못한 항목이 있습니다.\n\n"
            + "\n".join(blocked[:10]),
        )
        return

    session_labels = manual_ats_session_labels()
    selected_label_text = ", ".join(
        str(session_labels.get(key, key)) for key in selected_sessions
    )
    answer = QMessageBox.question(
        dialog_parent,
        f"ATS {method}매도 확인",
        f"선택한 수동운영 종목을 ATS {method} 방식으로 청산 요청하시겠습니까?\n\n"
        f"ATS 구간: {selected_label_text}\n"
        f"대상 종목: {len(selected)}개\n\n"
        "요청은 기존 주문 승인·Queue·Dispatch Claim·SendOrder 안전 경계를 통과합니다.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if answer != QMessageBox.Yes:
        window.statusBarMessage(f"ATS {method}매도 취소")
        return

    command_service = OperationCommandService(PROJECT_ROOT)
    completed: list[str] = []
    failed: list[str] = []
    for preview in previews:
        commit_result = commit_manual_ats_liquidation_preview(
            preview,
            project_root=PROJECT_ROOT,
        )
        code = str(preview.get("code") or "")
        name = str(preview.get("name") or "")
        stock_dir = str(preview.get("stock_dir") or "")
        command_id = str(preview.get("command_id") or "")
        order_id = str(commit_result.get("order_id") or "")
        if commit_result.get("ok") is not True:
            reasons = ", ".join(
                str(value) for value in commit_result.get("blocked_reasons", []) if value
            )
            failed.append(f"{code} {name}: {reasons or '주문후보 생성 실패'}")
            continue

        execution_result = window.process_executable_order_for_auto_trade(order_id)
        result_status = _manual_ats_result_status(execution_result)
        detail = ", ".join(
            str(value)
            for value in execution_result.get("blocked_reasons", [])
            if value
        )
        status_result = command_service.record_manual_ats_liquidation_status(
            stock_dir,
            command_id,
            result_status,
            order_id=order_id,
            detail=detail,
        )
        if status_result.status != "APPLIED":
            failed.append(
                f"{code} {name}: SendOrder 결과 Runtime read-back 기록 실패"
            )
            continue
        if result_status == "SEND_CALL_ACCEPTED":
            completed.append(f"{code} {name}")
            append_stock_log(
                Path(stock_dir),
                "GUI",
                f"수동운영 ATS {method}매도 SendOrder 접수: {selected_label_text}",
            )
        else:
            failed.append(f"{code} {name}: {detail or result_status}")

    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()
    window.refresh_all()
    window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
    window._runtime_file_snapshot = window.current_runtime_file_signature()
    window.update_action_buttons()

    if completed:
        window.statusBarMessage(
            f"ATS {method}매도 SendOrder 접수 기록: {len(completed)}개"
        )
    if failed:
        QMessageBox.warning(
            dialog_parent,
            f"ATS {method}매도 결과",
            "일부 또는 전체 종목의 ATS 청산 요청이 접수되지 않았습니다.\n\n"
            + "\n".join(failed[:10]),
        )
