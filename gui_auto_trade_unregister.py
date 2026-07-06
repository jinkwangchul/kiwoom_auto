# -*- coding: utf-8 -*-
"""
gui_auto_trade_unregister.py

자동매매설정창의 등록해제 처리 헬퍼.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QListWidget,
    QListWidgetItem,
)

from gui_auto_trade_utils import auto_trade_unregister_category
from gui_blocked_report_window import (
    blocked_items_preview,
    latest_blocked_action_report_path,
    write_blocked_action_report,
)
from runtime_io import read_json_dict
from gui_auto_trade_runtime import write_state_json
from gui_config_utils import default_orders, default_state
from gui_common_utils import safe_int_value
from gui_order_utils import pending_order_side_quantities, format_number_value
from gui_base_stock_service import update_base_stock_routines as update_base_stock_routines_from_service

PROJECT_ROOT = Path(__file__).resolve().parent
BASE_STOCK_PATH = PROJECT_ROOT / "기초종목.txt"
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


def update_base_stock_routines(code: str, name: str, routines: list[str]) -> bool:
    """
    등록해제 루틴 연결 갱신.

    중앙 종목관리 개편 이후 이 파일 안에서 기초종목.txt를 직접 수정하지 않는다.
    gui_base_stock_service.update_base_stock_routines()로 위임하여
    - stocks/ 중앙 구조가 있으면 stocks/종목/config.json 갱신
    - 아직 stocks/가 없으면 기존 기초종목.txt fallback
    흐름을 동일하게 사용한다.
    """
    return bool(update_base_stock_routines_from_service(code, name, routines))



class AutoTradeUnregisterConfirmDialog(QDialog):
    """자동매매설정 등록해제 가능/주의/불가 대상을 한 창에 표시한다."""

    def __init__(
        self,
        routine_name: str,
        immediate_items: list[dict[str, object]],
        force_items: list[dict[str, object]],
        blocked_items: list[dict[str, object]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("주의 종목 등록해제")
        self.resize(760, 560)
        self.force_items = force_items
        self.force_checkboxes: list[QCheckBox] = []

        main_layout = QVBoxLayout()

        summary_label = QLabel(
            f"즉시 등록해제 {len(immediate_items)}개 / 주의 등록해제 {len(force_items)}개 / 등록해제 불가 {len(blocked_items)}개"
        )
        summary_label.setMinimumHeight(44)
        main_layout.addWidget(summary_label)

        if blocked_items:
            blocked_title = QLabel("등록해제 불가")
            blocked_title.setStyleSheet("color: #d00000; font-weight: bold;")
            main_layout.addWidget(blocked_title)

            blocked_list = QListWidget()
            blocked_list.setMinimumHeight(110)
            for item in blocked_items:
                blocked_list.addItem(QListWidgetItem(self._blocked_line(item)))
            main_layout.addWidget(blocked_list)

        if force_items:
            force_title = QLabel("주의 등록해제")
            force_title.setStyleSheet("color: #b36b00; font-weight: bold;")
            main_layout.addWidget(force_title)

            force_box = QWidget()
            force_layout = QVBoxLayout()
            force_layout.setContentsMargins(0, 0, 0, 0)
            for item in force_items:
                checkbox = QCheckBox(self._force_line(item))
                checkbox.setChecked(False)
                self.force_checkboxes.append(checkbox)
                force_layout.addWidget(checkbox)
            force_box.setLayout(force_layout)
            main_layout.addWidget(force_box)

        if immediate_items:
            immediate_title = QLabel("즉시 등록해제 가능")
            immediate_title.setStyleSheet("font-weight: bold;")
            main_layout.addWidget(immediate_title)

            immediate_list = QListWidget()
            immediate_list.setMinimumHeight(100)
            for item in immediate_items:
                code = str(item.get("code", "")).strip()
                name = str(item.get("name", "")).strip()
                immediate_list.addItem(QListWidgetItem(f"{code} / {name}"))
            main_layout.addWidget(immediate_list)

        notice = QLabel(
            "※ 즉시 등록해제 가능 종목은 바로 처리됩니다.\n"
            "※ 주의 등록해제 종목은 체크한 항목만 처리됩니다.\n"
            "※ 등록해제 불가 종목은 처리불가 누적리포트에 기록됩니다."
        )
        notice.setStyleSheet("color: #555555;")
        main_layout.addWidget(notice)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.btn_confirm = QPushButton("등록해제 실행")
        self.btn_cancel = QPushButton("취소")
        self.btn_confirm.setMinimumWidth(130)
        self.btn_cancel.setMinimumWidth(100)
        self.btn_confirm.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_confirm)
        button_layout.addWidget(self.btn_cancel)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

    def _status_text(self, value: object) -> str:
        raw = str(value or "").strip()
        if not raw or raw == "-":
            return "-"
        try:
            return auto_trade_status_display(raw)
        except Exception:
            return "검토종목"

    def _reason_text(self, item: dict[str, object]) -> str:
        reasons = item.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        return ", ".join(str(reason) for reason in reasons if str(reason).strip()) or "-"

    def _blocked_line(self, item: dict[str, object]) -> str:
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        return f"{code} / {name}"

    def _force_line(self, item: dict[str, object]) -> str:
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        reason = self._reason_text(item)
        return f"{code} / {name} / {reason}"

    def selected_items(self) -> list[dict[str, object]]:
        selected: list[dict[str, object]] = []
        for index, checkbox in enumerate(self.force_checkboxes):
            if checkbox.isChecked() and index < len(self.force_items):
                selected.append(self.force_items[index])
        return selected


def reset_runtime_orders_for_force_unregister(stock_dir: Path) -> bool:
    """
    강제 등록해제 시 자동매매설정 표에 남는 매결/도결/미체결 흔적을 제거한다.

    정책:
    - 현재 화면과 판단 기준이 되는 orders.json 은 빈 주문 목록으로 초기화한다.
    - 기존 주문 기록은 즉시 삭제하지 않고 orders_archive.json 에 보존한다.
    - config.json, logs 폴더, 루틴 종목 폴더는 건드리지 않는다.
    """
    orders_path = stock_dir / "orders.json"
    archive_path = stock_dir / "orders_archive.json"

    current_orders = read_orders_data(orders_path)

    try:
        if current_orders:
            archive_data = read_json_dict(archive_path)
            archives = archive_data.get("archives", [])
            if not isinstance(archives, list):
                archives = []

            archives.append(
                {
                    "archived_at": now_text(),
                    "reason": "강제 등록해제 상태초기화로 orders.json 현재 표시/판단 흔적 초기화",
                    "orders": current_orders,
                }
            )

            archive_path.write_text(
                json.dumps({"archives": archives}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        orders_path.write_text(json.dumps(default_orders(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def reset_runtime_state_for_force_unregister(stock_dir: Path) -> bool:
    """
    강제 등록해제 시 runtime 폴더와 설정/로그는 유지하되,
    현재 운영상태(state.json)와 화면 판단용 주문 흔적(orders.json)을 초기화한다.
    """
    state_path = stock_dir / "state.json"
    state = read_json_dict(state_path)
    if not state:
        state = default_state()

    reset_values = {
        "status": "STOPPED",
        "trade_set_status": "WAIT_BUY",
        "current_set_no": 1,
        "current_round": 0,
        "avg_price": 0,
        "holding_qty": 0,
        "holding_amount": 0,
        "buy_count": 0,
        "last_buy_price": 0,
        "last_buy_time": "",
        "last_sell_time": "",
        "pending_order": False,
        "pending_qty": 0,
        "remaining_qty": 0,
        "unfilled_qty": 0,
        "buy_pending_qty": 0,
        "sell_pending_qty": 0,
        "paused_at": "",
        "resumed_at": "",
        "review_required": False,
        "review_reason": "",
        "missed_buy_signal_count": 0,
        "missed_sell_signal_count": 0,
        "pause_signal_check_status": "UNCHECKED",
        "ignore_signals_before": "",
        "updated_at": now_text(),
    }
    state.update(reset_values)

    try:
        if not reset_runtime_orders_for_force_unregister(stock_dir):
            return False
        if not write_state_json(stock_dir, state):
            return False
        return True
    except Exception:
        return False



def unregister_selected_auto_trade_stocks(window) -> None:
    """
    자동매매설정 창에서 선택 종목을 현재 루틴에서 등록해제한다.

    정책:
    - 기초종목.txt의 루틴 연결만 제거한다. 종목 자체는 기초종목에 남긴다.
    - 루틴 runtime 폴더, config.json, logs는 유지한다.
    - 정지/감시중 + 보유·미체결 없음은 즉시 등록해제한다.
    - 정지/감시중 + 보유 또는 현재 미체결 있음은 확인창에서 체크한 항목만 등록해제하고 state/orders 현재 흔적을 초기화한다.
    - 매수/매도, 매도만 등 매매 가능 상태는 등록해제 불가로 표시만 한다.
    """
    selected = window.selected_stock_infos()
    routine_name = window.current_selected_routine_name()

    if not selected or not routine_name:
        QMessageBox.warning(window, "선택 오류", "등록해제할 종목을 1개 이상 선택하세요.")
        return

    immediate_items: list[dict[str, object]] = []
    force_items: list[dict[str, object]] = []
    blocked_items: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for stock_dir, code, name in selected:
        key = (code, name)
        if key in seen:
            continue
        seen.add(key)
        item = auto_trade_unregister_category(routine_name, stock_dir, code, name)
        category = str(item.get("category", "blocked"))
        if category == "immediate":
            immediate_items.append(item)
        elif category == "force":
            force_items.append(item)
        else:
            blocked_items.append(item)

    selected_force_items: list[dict[str, object]] = []
    if immediate_items or force_items or blocked_items:
        dialog = AutoTradeUnregisterConfirmDialog(
            routine_name=routine_name,
            immediate_items=immediate_items,
            force_items=force_items,
            blocked_items=blocked_items,
            parent=window,
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        selected_force_items = dialog.selected_items()

    process_items = immediate_items + selected_force_items
    if not process_items:
        QMessageBox.information(window, "등록해제 없음", "등록해제 처리할 종목이 선택되지 않았습니다.")
        return

    reset_failed_items: list[str] = []
    completed_items: list[str] = []
    force_keys = {(str(item.get("code", "")), str(item.get("name", ""))) for item in selected_force_items}

    for item in process_items:
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        if not code or not name:
            continue

        if (code, name) in force_keys:
            for runtime_routine_name, stock_dir in item.get("runtime_dirs", []):
                if not reset_runtime_state_for_force_unregister(stock_dir):
                    reset_failed_items.append(f"{code} {name} / {runtime_routine_name}")
                    continue
                append_stock_log(
                    stock_dir,
                    "FORCE_ROUTINE_UNREGISTER_RESET",
                    "자동매매설정 등록해제로 state.json과 orders.json 현재 표시/판단값 초기화",
                )

        if reset_failed_items:
            continue

        if update_base_stock_routines(code, name, []):
            completed_items.append(f"{code},{name}")

    if reset_failed_items:
        preview_text = "\n".join(reset_failed_items[:10])
        if len(reset_failed_items) > 10:
            preview_text += f"\n... 외 {len(reset_failed_items) - 10}개"
        QMessageBox.warning(
            window,
            "상태 초기화 오류",
            "일부 종목의 state.json/orders.json 초기화에 실패했습니다.\n"
            "해당 종목은 루틴 등록해제를 완료하지 않았습니다.\n\n"
            f"{preview_text}",
        )
        return

    if not completed_items:
        QMessageBox.information(window, "등록해제 없음", "기초종목.txt에서 등록해제할 종목을 찾지 못했습니다.")
        return

    report_path = write_blocked_action_report(
        "자동매매설정 등록해제",
        blocked_items,
        target_routine=routine_name,
    )

    append_changelog(
        "UPDATE",
        "종목 루틴 연결",
        f"자동매매설정 창 루틴 등록해제: {' / '.join(completed_items)} / 중앙 종목관리 기준 갱신",
    )

    window.statusBar_message(f"루틴 등록해제 완료: {len(completed_items)}개")
    parent = window.parent()
    if parent is not None and hasattr(parent, "refresh_all"):
        parent.refresh_all()
    window.refresh_all()

    result_lines = [f"등록해제 완료: {len(completed_items)}개"]
    if blocked_items:
        result_lines.append(f"등록해제 불가: {len(blocked_items)}개")
        if report_path is not None:
            result_lines.append(f"리포트: {report_path.name}")
    QMessageBox.information(window, "등록해제 결과", "\n".join(result_lines))

