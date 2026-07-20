# -*- coding: utf-8 -*-
"""
gui_auto_trade_close.py

자동매매설정창의 조기마감/개별청산 처리 헬퍼.
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
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QGridLayout,
)

from gui_common_utils import safe_int_value
from gui_config_utils import default_config
from gui_order_utils import order_current_pending_qty
from gui_order_utils import order_datetime
from gui_order_utils import order_value
from gui_order_utils import pending_order_side_quantities
from gui_order_utils import read_orders_data
from runtime_io import read_json_dict
from state_policy import auto_trade_status_display
from gui_auto_trade_integrity import auto_trade_setting_data_inconsistency_reasons
from gui_auto_trade_policy import (
    operation_policy_section,
    auto_trade_setting_early_close_requested,
    auto_trade_setting_has_buy_pending_problem,
    auto_trade_setting_has_close_progress_quantity,
    auto_trade_setting_liquidation_phase_active,
    auto_trade_setting_trade_started,
    clear_early_close_runtime_metadata_only,
    close_method_from_state_or_policy,
    effective_liquidation_policy_for_config,
    auto_trade_setting_liquidation_text,
    short_close_method_text,
)
from operation_command_service import (
    EarlyCloseCompatibility,
    MODE_EARLY_CLOSE,
    MODE_NORMAL,
    OperationCommandRequest,
    OperationCommandService,
    RESULT_FAILED,
    RESULT_SUCCESS,
    STOCK_APPLIED,
    SCOPE_STOCK,
)


PROJECT_ROOT = Path(__file__).resolve().parent
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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



class ProfitLossEarlyCloseDialog(QDialog):
    """우클릭 조기마감 > 손/익절 입력창.

    환경설정의 입력 방식과 맞춰 한 줄에
    "익절/손절 + [익절] / - [손절]" 형태로 입력한다.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("손/익절 조기마감")
        self.resize(330, 120)

        layout = QVBoxLayout()
        guide = QLabel("익절/손절 비율(%)을 입력하세요.")
        layout.addWidget(guide)

        row_layout = QHBoxLayout()
        self.enabled_check = QCheckBox("익절/손절")
        self.enabled_check.setChecked(True)
        self.enabled_check.setEnabled(False)
        row_layout.addWidget(self.enabled_check)

        row_layout.addWidget(QLabel("+"))
        self.profit_edit = QLineEdit()
        self.profit_edit.setPlaceholderText("입력")
        self.profit_edit.setMaximumWidth(70)
        row_layout.addWidget(self.profit_edit)

        row_layout.addWidget(QLabel("/ -"))
        self.loss_edit = QLineEdit()
        self.loss_edit.setPlaceholderText("입력")
        self.loss_edit.setMaximumWidth(70)
        row_layout.addWidget(self.loss_edit)
        row_layout.addStretch(1)
        layout.addLayout(row_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("확인")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def values(self) -> tuple[str, str]:
        return self.profit_edit.text().strip(), self.loss_edit.text().strip()

    def _positive_number_from_text(self, value: str) -> float:
        # 입력창 앞에 + / - 라벨이 있으므로 사용자가 부호를 입력해도 절댓값으로 해석한다.
        return abs(float(value))

    def accept(self) -> None:
        profit_text, loss_text = self.values()
        if not profit_text and not loss_text:
            QMessageBox.warning(
                self,
                "입력 필요",
                "익절 또는 손절 비율 중 최소 1개 값을 입력하세요.",
            )
            self.profit_edit.setFocus()
            return

        for label, value, widget in [
            ("익절", profit_text, self.profit_edit),
            ("손절", loss_text, self.loss_edit),
        ]:
            if not value:
                continue
            try:
                number = self._positive_number_from_text(value)
            except ValueError:
                QMessageBox.warning(self, "입력 오류", f"{label} 비율은 숫자로 입력하세요.")
                widget.setFocus()
                widget.selectAll()
                return
            if number <= 0:
                QMessageBox.warning(self, "입력 오류", f"{label} 비율은 0보다 큰 값으로 입력하세요.")
                widget.setFocus()
                widget.selectAll()
                return

        super().accept()


class IndividualLiquidationSettingsDialog(QDialog):
    """종목별 개별 청산 설정창.

    저장 위치: 각 종목 config.json / individual_liquidation
    - 환경설정 사용: enabled=False
    - 개별 청산 사용: enabled=True + minutes/method 저장
    - 청산 안함(이월): enabled=True + method=이월 저장
    """

    MODES = ["환경설정 사용", "개별 청산 사용", "청산 안함(이월)"]
    MINUTES = [str(value) for value in range(1, 101)]
    METHODS = ["시장가", "현재가"]

    def __init__(
        self,
        initial_policy: dict[str, object] | None = None,
        target_count: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("개별 청산")
        self.resize(310, 145)

        policy = initial_policy if isinstance(initial_policy, dict) else {}
        enabled = bool(policy.get("enabled", False))
        method = str(policy.get("method", "시장가")).strip() or "시장가"
        minutes = str(policy.get("minutes_before_regular_close", "5")).strip() or "5"

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        label_apply = QLabel("적용")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(self.MODES)
        mode_width = max(self.mode_combo.fontMetrics().horizontalAdvance(text) for text in self.MODES) + 44
        self.mode_combo.setFixedWidth(mode_width)
        self.mode_combo.setMinimumHeight(28)
        if not enabled:
            self.mode_combo.setCurrentText("환경설정 사용")
        elif method == "이월":
            self.mode_combo.setCurrentText("청산 안함(이월)")
        else:
            self.mode_combo.setCurrentText("개별 청산 사용")
        form.addWidget(label_apply, 0, 0)
        form.addWidget(self.mode_combo, 0, 1, 1, 3)

        self.label_minutes = QLabel("정규장 종료")
        self.minutes_combo = QComboBox()
        self.minutes_combo.addItems(self.MINUTES)
        if minutes not in self.MINUTES:
            self.minutes_combo.addItem(minutes)
        self.minutes_combo.setCurrentText(minutes)
        self.minutes_combo.setFixedWidth(64)
        self.minutes_combo.setMinimumHeight(28)
        self.label_minutes_suffix = QLabel("분전")
        form.addWidget(self.label_minutes, 1, 0)
        form.addWidget(self.minutes_combo, 1, 1)
        form.addWidget(self.label_minutes_suffix, 1, 2)

        self.label_method = QLabel("방식")
        self.method_combo = QComboBox()
        self.method_combo.addItems(self.METHODS)
        method_width = max(self.method_combo.fontMetrics().horizontalAdvance(text) for text in self.METHODS) + 44
        self.method_combo.setFixedWidth(method_width)
        self.method_combo.setMinimumHeight(28)
        if method in self.METHODS:
            self.method_combo.setCurrentText(method)
        form.addWidget(self.label_method, 2, 0)
        form.addWidget(self.method_combo, 2, 1, 1, 2)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.mode_combo.currentTextChanged.connect(self.update_enabled_state)
        self.setLayout(layout)
        self.update_enabled_state()

    def update_enabled_state(self) -> None:
        mode = self.mode_combo.currentText()
        show_detail = mode == "개별 청산 사용"
        for widget in [
            self.label_minutes,
            self.minutes_combo,
            self.label_minutes_suffix,
            self.label_method,
            self.method_combo,
        ]:
            widget.setVisible(show_detail)

    def values(self) -> dict[str, object]:
        mode = self.mode_combo.currentText()
        if mode == "환경설정 사용":
            return {
                "enabled": False,
                "minutes_before_regular_close": "",
                "method": "",
            }
        if mode == "청산 안함(이월)":
            return {
                "enabled": True,
                "minutes_before_regular_close": "",
                "method": "이월",
            }
        return {
            "enabled": True,
            "minutes_before_regular_close": str(self.minutes_combo.currentText()).strip() or "5",
            "method": str(self.method_combo.currentText()).strip() or "시장가",
        }



def auto_trade_open_selected_individual_liquidation_settings(window) -> None:
    """선택 종목의 개별 청산 설정을 저장한다."""
    selected = window.selected_stock_infos()
    if not selected:
        QMessageBox.warning(window, "선택 오류", "개별 청산을 설정할 종목을 선택하세요.")
        return

    first_config = read_json_dict(selected[0][0] / "config.json")
    initial = first_config.get("individual_liquidation", {}) if isinstance(first_config, dict) else {}
    dialog = IndividualLiquidationSettingsDialog(initial, len(selected), window)
    if dialog.exec_() != QDialog.Accepted:
        return

    policy_values = dialog.values()
    changed_count = auto_trade_save_selected_individual_liquidation_settings(window, policy_values)
    mode_text = window.individual_liquidation_status_text(policy_values)
    window.statusBarMessage(f"개별 청산 저장 완료: {mode_text} / 대상 {changed_count}개")



def auto_trade_save_selected_individual_liquidation_settings(window, policy_values: dict[str, object]) -> int:
    selected = window.selected_stock_infos()
    if not selected:
        return 0

    normalized = {
        "enabled": bool(policy_values.get("enabled", False)),
        "minutes_before_regular_close": str(policy_values.get("minutes_before_regular_close", "")).strip(),
        "method": str(policy_values.get("method", "")).strip(),
        "updated_at": now_text(),
    }
    if not normalized["enabled"]:
        normalized["minutes_before_regular_close"] = ""
        normalized["method"] = ""
    elif normalized["method"] == "이월":
        normalized["minutes_before_regular_close"] = normalized["minutes_before_regular_close"] or "5"
    else:
        normalized["minutes_before_regular_close"] = normalized["minutes_before_regular_close"] or "5"
        normalized["method"] = short_close_method_text(normalized["method"]) or "시장가"

    changed_count = 0
    for stock_dir, code, name in selected:
        config_path = stock_dir / "config.json"
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = default_config()

        config["individual_liquidation"] = dict(normalized)
        try:
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            QMessageBox.critical(
                window,
                "개별 청산 저장 오류",
                f"{code} {name} 개별 청산 저장 중 오류가 발생했습니다.\n\n{exc}",
            )
            continue

        append_stock_log(stock_dir, "GUI", f"개별 청산 저장: {window.individual_liquidation_status_text(normalized)}")
        changed_count += 1

    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()
    window.load_selected_routine_stocks()
    window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
    window._runtime_file_snapshot = window.current_runtime_file_signature()
    window.update_action_buttons()
    return changed_count



def auto_trade_apply_selected_early_close_default(window) -> None:
    """외부 조기마감 버튼: 환경설정의 조기마감 디폴트값을 선택 종목에 즉시 적용한다."""
    method = str(operation_policy_section("early_close").get("method", "루틴")).strip() or "루틴"
    window.apply_selected_early_close(method, source="디폴트값")



def auto_trade_apply_selected_early_close_profit_loss(window) -> None:
    """우클릭 조기마감 > 손/익절: 익절/손절 비율을 분리 입력 후 전환한다."""
    dialog = ProfitLossEarlyCloseDialog(window)
    if dialog.exec_() != QDialog.Accepted:
        return

    profit_text, loss_text = dialog.values()
    window.apply_selected_early_close(
        "손/익절",
        source="우클릭",
        extra_policy={
            "profit_percent": profit_text,
            "loss_percent": loss_text,
        },
    )



def _read_runtime_order_queue() -> tuple[list[dict[str, object]], str]:
    if not ORDER_QUEUE_PATH.exists():
        return [], ""
    try:
        data = json.loads(ORDER_QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"queue_read_failed:{exc}"
    if not isinstance(data, dict):
        return [], "queue_root_invalid"
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        return [], "queue_orders_invalid"
    if not all(isinstance(order, dict) for order in orders):
        return [], "queue_order_invalid"
    return orders, ""


def _nested_text_values(value: object) -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(_nested_text_values(child))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for child in value:
            result.extend(_nested_text_values(child))
        return result
    return [str(value or "").strip()]


def _record_mentions_code(record: dict[str, object], code: str) -> bool:
    if not code:
        return False
    return any(text == code for text in _nested_text_values(record))


def _truthy_record_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() not in {"", "0", "false", "none", "no", "n"}


def _queue_execution_evidence_for_code(code: str) -> str:
    records, read_error = _read_runtime_order_queue()
    if read_error:
        return read_error
    for record in records:
        if not _record_mentions_code(record, code):
            continue
        status = str(record.get("status") or "").strip().upper()
        if status == "ORDER_QUEUED":
            return "ORDER_QUEUED"
        for key in (
            "dispatch_id",
            "claim_token",
            "claimed_at",
            "send_order_attempt_id",
            "send_order_called_at",
            "broker_order_no",
            "broker_status",
        ):
            if str(record.get(key) or "").strip():
                return key
        for key in ("send_order_called", "execution_enabled"):
            if _truthy_record_value(record.get(key)):
                return key
    return ""


def _order_is_after_early_close(order: dict[str, object], requested_at: str) -> tuple[bool, str]:
    order_dt = order_datetime(order)
    if order_dt is None:
        return False, "order_time_unknown"
    try:
        requested_dt = datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
    except Exception:
        try:
            requested_dt = datetime.strptime(requested_at, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return False, "early_close_time_unknown"
    if order_dt.tzinfo is not None and requested_dt.tzinfo is None:
        order_dt = order_dt.replace(tzinfo=None)
    if order_dt.tzinfo is None and requested_dt.tzinfo is not None:
        requested_dt = requested_dt.replace(tzinfo=None)
    return order_dt >= requested_dt, ""


def _stock_order_execution_evidence(stock_dir: Path, requested_at: str) -> str:
    for order in read_orders_data(stock_dir / "orders.json"):
        side = str(order_value(order, ["side", "order_side", "구분", "매매구분"], "")).strip().upper()
        if side not in {"SELL", "매도", "S"}:
            continue
        is_after, time_error = _order_is_after_early_close(order, requested_at)
        evidence_fields = (
            "id",
            "order_id",
            "broker_order_no",
            "dispatch_id",
            "send_order_attempt_id",
            "send_order_called_at",
            "send_order_status",
        )
        has_identity = any(str(order.get(key) or "").strip() for key in evidence_fields)
        has_send_order = _truthy_record_value(order.get("send_order_called"))
        filled_qty = safe_int_value(order_value(order, ["filled_qty", "executed_qty", "체결수량"], 0), 0)
        pending_qty, pending_unknown = order_current_pending_qty(order)
        if time_error and (has_identity or has_send_order or filled_qty > 0 or pending_qty > 0 or pending_unknown):
            return time_error
        if not is_after:
            continue
        if has_identity:
            return "order_identity"
        if has_send_order:
            return "send_order_called"
        if filled_qty > 0:
            return "filled_qty"
        if pending_unknown or pending_qty > 0:
            return "pending_order"
    return ""


def _selected_display_status_by_stock_dir(window) -> dict[str, str]:
    table = getattr(window, "stock_table", None)
    if table is None:
        return {}
    try:
        selected_rows = list(table.selectionModel().selectedRows())
    except Exception:
        return {}

    result: dict[str, str] = {}
    for index in selected_rows:
        try:
            row = index.row()
            path_item = table.item(row, 0)
            status_item = table.item(row, 4)
        except Exception:
            continue
        if path_item is None or status_item is None:
            continue
        try:
            path_text = str(path_item.data(Qt.UserRole) or "").strip()
        except Exception:
            path_text = ""
        if not path_text:
            continue
        try:
            key = str(Path(path_text).resolve())
        except Exception:
            key = path_text
        result[key] = str(status_item.text() or "").strip()
    return result


def _early_close_cancel_block_reason(
    stock_dir: Path,
    code: str,
    state: dict[str, object],
    *,
    display_status: str = "",
) -> str:
    if not isinstance(state, dict) or not state:
        return "state_read_failed"
    if not auto_trade_setting_trade_started(state):
        return "trade_not_started"
    if not auto_trade_setting_early_close_requested(state):
        return "early_close_not_active"
    if display_status and display_status != "조기마감":
        return "display_not_early_close"
    requested_at = str(state.get("early_close_requested_at") or "").strip()
    if not requested_at:
        return "early_close_identity_missing"
    if str(state.get("close_routine_final_sell_ordered_at") or "").strip():
        return "final_sell_ordered_at"
    if bool(state.get("close_routine_final_sell_ordered", False)):
        return "final_sell_ordered"
    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        return "pending_unknown"
    if safe_int_value(buy_pending_qty, 0) > 0 or safe_int_value(sell_pending_qty, 0) > 0:
        return "pending_order"
    queue_evidence = _queue_execution_evidence_for_code(code)
    if queue_evidence:
        return queue_evidence
    order_evidence = _stock_order_execution_evidence(stock_dir, requested_at)
    if order_evidence:
        return order_evidence
    return ""


def auto_trade_cancel_selected_early_close(window) -> None:
    """우클릭 조기마감 > 취소.

    실행 증거가 없는 조기마감 명령만 기존 OperationCommandService의 NORMAL
    적용 경로로 철회한다. 안전성을 확인할 수 없으면 fail-closed로 차단한다.
    """
    blocked_message = "현재 상태는 마감정책 취소 대상이 아닙니다."
    success_message = "마감정책이 취소되었습니다."
    notify = getattr(window, "showAutoTradePopupMessage", None)
    if not callable(notify):
        notify = window.statusBarMessage
    selected = window.selected_stock_infos()
    if not selected:
        notify(blocked_message)
        return

    states: list[tuple[Path, str, str, dict[str, object]]] = []
    block_reasons: list[tuple[Path, str, str, str]] = []
    display_status_by_stock_dir = _selected_display_status_by_stock_dir(window)
    for stock_dir, code, name in selected:
        state = read_json_dict(stock_dir / "state.json")
        stock_dir_key = str(stock_dir.resolve())
        reason = _early_close_cancel_block_reason(
            stock_dir,
            code,
            state,
            display_status=display_status_by_stock_dir.get(stock_dir_key, ""),
        )
        if reason:
            block_reasons.append((stock_dir, code, name, reason))
            continue
        states.append((stock_dir, code, name, state))

    if block_reasons or not states:
        for stock_dir, code, name, reason in block_reasons:
            append_stock_log(stock_dir, "GUI", f"조기마감 취소 차단: {code} {name} / {reason}")
        notify(blocked_message)
        return

    command_service = OperationCommandService(PROJECT_ROOT)
    completed: list[str] = []
    failed: list[tuple[Path, str, str, str]] = []
    for stock_dir, code, name, _state in states:
        result = command_service.apply(
            OperationCommandRequest(
                target_scope=SCOPE_STOCK,
                target_id=str(stock_dir.resolve()),
                command=MODE_NORMAL,
                source="우클릭",
            )
        )
        if result.status != RESULT_SUCCESS or not result.stock_results or result.stock_results[0].status != STOCK_APPLIED:
            reason = result.error or (result.stock_results[0].error if result.stock_results else "cancel_failed")
            failed.append((stock_dir, code, name, reason))
            continue
        saved = read_json_dict(stock_dir / "state.json")
        if auto_trade_setting_early_close_requested(saved):
            failed.append((stock_dir, code, name, "read_back_early_close_still_active"))
            continue
        completed.append(f"{code} {name}")
        append_stock_log(stock_dir, "GUI", "조기마감 취소 완료")

    if failed or not completed:
        for stock_dir, code, name, reason in failed:
            append_stock_log(stock_dir, "GUI", f"조기마감 취소 차단: {code} {name} / {reason}")
        notify(blocked_message)
        return

    append_changelog("UPDATE", "state.json", f"조기마감 취소: {' / '.join(completed)}")
    window.refresh_all()
    window.stock_table.viewport().update()
    window.stock_table.repaint()
    notify(success_message)


def auto_trade_apply_selected_early_close(
    window,
    method: str,
    source: str = "우클릭",
    extra_policy: dict[str, object] | None = None,
) -> None:
    """선택 종목에 조기마감 명령을 적용한다.

    조기마감은 보유수량을 0으로 만드는 1차 리셋 절차다.
    대상 기준은 보유수량이며, 미수/미도/미체결은 대상 판정 기준으로 쓰지 않는다.
    루틴 방식 조기마감은 첫 매도신호 전까지 매수/매도 신호를 허용하고,
    첫 매도주문 접수 이후 추가 주문 차단은 메인 주문판정 계층에서 처리한다.
    """
    selected = window.selected_stock_infos()
    routine_name = window.current_selected_routine_name()

    def show_ok_message(icon, title: str, message: str) -> None:
        box = QMessageBox(window)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(message)
        ok_button = box.addButton("확인", QMessageBox.AcceptRole)
        box.setDefaultButton(ok_button)
        box.exec_()

    if not selected or not routine_name:
        show_ok_message(
            QMessageBox.Warning,
            "선택 오류",
            "조기마감할 종목을 1개 이상 선택하세요.",
        )
        return

    method_text = str(method or "").strip() or "루틴"

    blocked_liquidation: list[str] = []
    close_targets: list[tuple[Path, str, str]] = []
    review_items: list[str] = []
    no_target_items: list[str] = []
    skipped_preview_items: list[str] = []

    for stock_dir, code, name in selected:
        state = read_json_dict(stock_dir / "state.json")
        config = read_json_dict(stock_dir / "config.json")
        if not config:
            config = default_config()

        status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
        if status in {
            "EMERGENCY_STOPPED",
            "EMERGENCY_STOP",
            "EMERGENCY",
            "REVIEW_REQUIRED",
            "REVIEW",
        }:
            skipped_preview_items.append(f"{code} {name}({auto_trade_status_display(status)})")
            continue

        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        if auto_trade_setting_liquidation_phase_active(config, holding_qty, state=state):
            blocked_liquidation.append(f"{code} {name}")
            continue

        _buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
        has_close_progress_qty = auto_trade_setting_has_close_progress_quantity(
            holding_qty,
            sell_pending_qty,
        )

        if has_close_progress_qty:
            close_targets.append((stock_dir, code, name))
        else:
            no_target_items.append(f"{code} {name}")

    if blocked_liquidation:
        preview_blocked = "\n".join(f"- {item}" for item in blocked_liquidation[:8])
        if len(blocked_liquidation) > 8:
            preview_blocked += f"\n- 외 {len(blocked_liquidation) - 8}개"
        show_ok_message(
            QMessageBox.Warning,
            "조기마감 불가",
            "청산 절차가 시작된 종목은 조기마감으로 변경할 수 없습니다.\n\n"
            f"대상:\n{preview_blocked}",
        )
        window.statusBarMessage("조기마감 불가: 청산 진행 중")
        return

    # 보유가 없는 경우는 사용자의 재확인 대상이 아니다.
    # 조기마감은 보유수량을 0으로 만드는 1차 리셋 절차이므로,
    # 보유가 없으면 조기마감 절차를 생략하고 감시/대기 + 현황 주황으로 정리한다.
    if not close_targets and not review_items:
        preview_no_target = "\n".join(f"- {item}" for item in no_target_items[:8])
        if len(no_target_items) > 8:
            preview_no_target += f"\n- 외 {len(no_target_items) - 8}개"
        if skipped_preview_items:
            skipped_preview = "\n".join(f"- {item}" for item in skipped_preview_items[:5])
            if len(skipped_preview_items) > 5:
                skipped_preview += f"\n- 외 {len(skipped_preview_items) - 5}개"
            preview_no_target += f"\n\n제외:\n{skipped_preview}"
        if not preview_no_target.strip():
            preview_no_target = "대상 없음"

        show_ok_message(
            QMessageBox.Information,
            "조기마감 생략",
            "선택 종목에 보유 대상이 없습니다.\n\n"
            "조기마감 절차는 수행하지 않고\n"
            "감시/대기 상태로 전환합니다.\n\n"
            f"대상:\n{preview_no_target}",
        )
    else:
        preview_parts: list[str] = []
        if close_targets:
            target_preview = "\n".join(f"- {code} {name}" for _, code, name in close_targets[:8])
            if len(close_targets) > 8:
                target_preview += f"\n- 외 {len(close_targets) - 8}개"
            preview_parts.append(f"조기마감 진행 대상:\n{target_preview}")
        if no_target_items:
            no_target_preview = "\n".join(f"- {item}" for item in no_target_items[:5])
            if len(no_target_items) > 5:
                no_target_preview += f"\n- 외 {len(no_target_items) - 5}개"
            preview_parts.append(f"조기마감 생략 대상:\n{no_target_preview}")

        preview = "\n\n".join(preview_parts) if preview_parts else "대상 없음"

        box = QMessageBox(window)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("조기마감 확인")
        box.setText(
            f"선택 종목의 조기마감 절차를 시작합니다.\n\n"
            f"방식: {method_text}\n\n"
            f"대상:\n{preview}\n\n"
            "진행하시겠습니까?"
        )
        proceed_button = box.addButton("진행", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.setDefaultButton(proceed_button)
        box.exec_()
        if box.clickedButton() != proceed_button:
            window.statusBarMessage("조기마감 취소")
            return

    completed: list[str] = []
    skipped: list[str] = []
    command_service = OperationCommandService(PROJECT_ROOT)

    for stock_dir, code, name in selected:
        state = read_json_dict(stock_dir / "state.json")
        status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
        if status in {
            "EMERGENCY_STOPPED",
            "EMERGENCY_STOP",
            "EMERGENCY",
            "REVIEW_REQUIRED",
            "REVIEW",
        }:
            skipped.append(f"{code} {name}({auto_trade_status_display(status)})")
            continue

        config = read_json_dict(stock_dir / "config.json")
        if not config:
            config = default_config()

        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
        holding_qty = safe_int_value(state.get("holding_qty"), 0)

        if auto_trade_setting_liquidation_phase_active(config, holding_qty, state=state):
            skipped.append(f"{code} {name}(청산 진행 중)")
            continue

        has_close_progress_qty = auto_trade_setting_has_close_progress_quantity(
            holding_qty,
            sell_pending_qty,
        )
        command_result = command_service.apply_early_close(
            OperationCommandRequest(
                target_scope=SCOPE_STOCK,
                target_id=str(stock_dir.resolve()),
                command=MODE_EARLY_CLOSE,
                source=source,
            ),
            EarlyCloseCompatibility(
                method=method_text,
                policy=dict(extra_policy or {}),
                has_close_progress_quantity=has_close_progress_qty,
            ),
        )
        if command_result.status == RESULT_FAILED or command_result.failed:
            reason = command_result.error
            if command_result.failed:
                reason = command_result.failed[0].error or reason
            skipped.append(f"{code} {name}({reason or '명령 적용 실패'})")
            continue

        completed.append(f"{code} {name}")
        if command_result.stock_results and command_result.stock_results[0].status == STOCK_APPLIED:
            log_reason = f"조기마감/{method_text}/마감진행" if has_close_progress_qty else "조기마감 대상 없음"
            append_stock_log(stock_dir, "GUI", f"자동매매 상태 변경: {log_reason}")

    if completed or skipped:
        changelog_parts: list[str] = []
        if completed:
            changelog_parts.append(f"조기마감({method_text}): {' / '.join(completed)}")
        if skipped:
            changelog_parts.append(f"제외: {' / '.join(skipped)}")
        append_changelog(
            "UPDATE",
            "state.json",
            f"조기마감 상태 변경: {routine_name} -> {' | '.join(changelog_parts)}",
        )

    window.refresh_all()
    window.stock_table.viewport().update()
    window.stock_table.repaint()

    message = f"조기마감 적용: {len(completed)}개"
    if skipped:
        message += f" / 제외 {len(skipped)}개"
    window.statusBarMessage(message)
