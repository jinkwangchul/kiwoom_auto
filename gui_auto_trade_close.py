# -*- coding: utf-8 -*-
"""
gui_auto_trade_close.py

자동매매설정창의 조기마감/개별청산 처리 헬퍼.
"""

from __future__ import annotations

import json
import logging
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
)
from gui_operation_ui_context import operation_dialog_parent

from gui_common_utils import safe_int_value
from gui_config_utils import default_config
from gui_order_utils import order_current_pending_qty
from gui_order_utils import order_datetime
from gui_order_utils import order_value
from gui_order_utils import pending_order_side_quantities
from gui_order_utils import read_orders_data
from runtime_io import read_json_dict
from gui_auto_trade_runtime import parse_stock_folder_name
from gui_auto_trade_table_loader import _selected_instance_stock_dirs
from gui_toast import show_toast
from state_policy import auto_trade_status_display
from gui_auto_trade_integrity import (
    auto_trade_setting_data_inconsistency_reasons,
    is_emergency_stopped_state,
    is_review_required_state,
)
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
from close_liquidation_transition_service import (
    DOMAIN_CLOSE,
    DOMAIN_LIQUIDATION,
    POLICY_ROUTINE_CLOSE,
)
from close_liquidation_execution_pipeline import (
    build_close_liquidation_candidate_preview,
    commit_close_liquidation_candidate_preview,
    normalize_direct_liquidation_method,
)
from close_intent_service import CLOSE_INTENT_EARLY_CLOSE, apply_close_intent
from operation_close_completion_check_service import (
    SOURCE_EARLY_CLOSE_DURABLE_UPDATE,
    check_global_close_completion_after_durable_update,
)
from operation_command_service import (
    COMMAND_INDIVIDUAL_LIQUIDATION,
    IndividualLiquidationOverride,
    MODE_EARLY_CLOSE,
    MODE_NORMAL,
    OperationCommandRequest,
    OperationCommandService,
    RESULT_FAILED,
    RESULT_SUCCESS,
    STOCK_APPLIED,
    SCOPE_STOCK,
)
from transition_evidence_reader import (
    COMMAND_REQUEST_SCOPE,
    TransitionEvidenceScope,
)
from transition_production_guard import evaluate_production_transition
from execution_queue_writer import read_execution_queue_records


PROJECT_ROOT = Path(__file__).resolve().parent
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
LOGGER = logging.getLogger(__name__)
EXPECTED_USER_ACTION_RECOVERY_BLOCK_REASONS = frozenset(
    {
        "RECOVERY_CONTEXT_MISSING",
        "RECOVERY_NOT_STARTED",
        "RECOVERY_IN_PROGRESS",
    }
)

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _production_recovery_gate(window, code: str, caller_name: str):
    try:
        parent = window.parent()
    except Exception:
        return None
    checker = getattr(type(parent), "production_recovery_gate_for_stock", None)
    if not callable(checker):
        return None
    return checker(parent, code, caller_name=caller_name)


def _log_recovery_block(
    window,
    *,
    code: str,
    caller_name: str,
    recovery,
    routine_instance_id: str = "",
) -> None:
    reason_code = str(getattr(recovery, "reason_code", "") or "").strip()
    evidence = tuple(getattr(recovery, "evidence", ()) or ())
    has_internal_error_evidence = any(
        str(item).startswith(("registry_error=", "gate_exception="))
        for item in evidence
    )
    if (
        reason_code in EXPECTED_USER_ACTION_RECOVERY_BLOCK_REASONS
        and not has_internal_error_evidence
    ):
        return
    try:
        parent = window.parent()
    except Exception:
        parent = None
    api = getattr(parent, "kiwoom_api", None)
    login_session_reader = getattr(api, "login_session_id", None)
    login_session_present = False
    if callable(login_session_reader):
        try:
            login_session_present = bool(
                str(login_session_reader() or "").strip()
            )
        except Exception:
            login_session_present = False
    account_reader = getattr(parent, "selected_account_no", None)
    account_selected = False
    if callable(account_reader):
        try:
            account_selected = bool(str(account_reader() or "").strip())
        except Exception:
            account_selected = False
    LOGGER.warning(
        "Auto-trade operation blocked by Production Recovery: "
        "caller=%s routine_instance=%s stock=%s reason=%s evidence=%s "
        "login_session_present=%s account_selected=%s requested_at=%s",
        caller_name,
        routine_instance_id,
        code,
        reason_code,
        evidence,
        login_session_present,
        account_selected,
        now_text(),
    )


def _recovery_block_user_message(window, recovery) -> str:
    try:
        parent = window.parent()
    except Exception:
        parent = None
    formatter = getattr(
        type(parent),
        "production_recovery_block_user_message",
        None,
    )
    if callable(formatter):
        try:
            message = str(formatter(parent, recovery) or "").strip()
            if message:
                return message
        except Exception:
            LOGGER.exception("Production Recovery 사용자 메시지 생성 실패")
    return "운영 상태 확인이 완료되지 않았습니다. 잠시 후 다시 시도해 주세요."


def _transition_trade_date(timestamp: object, fallback: str) -> str:
    text = str(timestamp or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return fallback[:10]


def _current_close_transition_policy(
    state: dict[str, object],
    requested_at: str,
) -> tuple[str, str]:
    early_method = str(state.get("early_close_method") or "").strip()
    early_policy = state.get("early_close_policy")
    if not early_method and isinstance(early_policy, dict):
        early_method = str(early_policy.get("method") or "").strip()
    early_started_at = str(state.get("early_close_requested_at") or "").strip()
    if early_method or early_started_at:
        return early_method or POLICY_ROUTINE_CLOSE, early_started_at or requested_at

    auto_method = str(state.get("auto_close_method") or "").strip()
    auto_policy = state.get("auto_close_policy")
    if not auto_method and isinstance(auto_policy, dict):
        auto_method = str(auto_policy.get("method") or "").strip()
    auto_started_at = str(state.get("auto_close_requested_at") or "").strip()
    if auto_method or auto_started_at:
        return auto_method, auto_started_at or requested_at

    return POLICY_ROUTINE_CLOSE, requested_at


def _command_transition_scope(
    *,
    code: str,
    routine_instance_id: str,
    started_at: str,
    requested_at: str,
    operation_command_id: str = "",
) -> TransitionEvidenceScope:
    return TransitionEvidenceScope(
        scope_type=COMMAND_REQUEST_SCOPE,
        stock_code=code,
        trade_date=_transition_trade_date(started_at, requested_at),
        routine_instance_id=routine_instance_id,
        transition_requested_at=started_at,
        operation_command_id=operation_command_id,
    )


def _close_liquidation_cancel_required(method: object) -> bool:
    return short_close_method_text(method) in {"시장가", "현재가", "손/익절"}


def _close_execution_result(
    execution_result: dict[str, object],
) -> dict[str, object]:
    send_result = execution_result.get("send_order_result")
    send_result = send_result if isinstance(send_result, dict) else {}
    send_status = str(send_result.get("status") or "").strip().upper()
    if send_status in {"SEND_CALL_REJECTED", "SEND_UNCERTAIN"}:
        return {
            "ok": False,
            "stage": send_status.lower(),
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": list(
                send_result.get("blocked_reasons")
                or [f"SendOrder result requires review: {send_status}"]
            ),
            "execution_result": execution_result,
        }
    return {
        "ok": execution_result.get("processed") is True,
        "stage": str(execution_result.get("stage") or "execution"),
        "runtime_status": (
            "EARLY_CLOSING"
            if execution_result.get("processed") is True
            else "REVIEW_REQUIRED"
        ),
        "blocked_reasons": list(
            execution_result.get("blocked_reasons") or []
        ),
        "execution_result": execution_result,
    }


def _resume_existing_close_order(
    window,
    record: dict[str, object],
    *,
    holding_qty: int,
) -> dict[str, object]:
    status = str(record.get("status") or "").strip().upper()
    order_id = str(record.get("id") or record.get("order_id") or "").strip()

    if holding_qty <= 0:
        return {
            "ok": True,
            "stage": "completed",
            "runtime_status": "EARLY_CLOSED",
            "order_id": order_id,
            "queue_status": status,
        }
    if status == "EXECUTABLE":
        return _close_execution_result(
            window.process_executable_order_for_auto_trade(order_id)
        )
    if status == "ORDER_QUEUED":
        send_result = window.send_order_for_order_queued_automatically(order_id)
        return _close_execution_result(
            {
                "processed": send_result.get("queue_result_recorded") is True,
                "stage": "send_order",
                "blocked_reasons": list(
                    send_result.get("blocked_reasons")
                    or send_result.get("issues")
                    or []
                ),
                "send_order_result": send_result,
            }
        )
    if status in {
        "SEND_CALL_ACCEPTED",
        "BROKER_ACCEPTED",
        "PARTIALLY_FILLED",
        "DISPATCH_CLAIMED",
        "SEND_ORDER_CALLED",
    }:
        return {
            "ok": True,
            "stage": "order_progress",
            "runtime_status": "EARLY_CLOSING",
            "order_id": order_id,
            "queue_status": status,
        }
    if status == "FILLED":
        return {
            "ok": False,
            "stage": "filled_holding_mismatch",
            "runtime_status": "REVIEW_REQUIRED",
            "order_id": order_id,
            "queue_status": status,
            "blocked_reasons": [
                "filled close order still has a positive Runtime holding quantity"
            ],
        }
    return {
        "ok": False,
        "stage": "order_pipeline_terminal",
        "runtime_status": "REVIEW_REQUIRED",
        "order_id": order_id,
        "queue_status": status,
        "blocked_reasons": [
            f"close order pipeline cannot continue from status {status or 'UNKNOWN'}"
        ],
    }


def _persist_early_close_execution_result(
    window,
    *,
    stock_dir: Path,
    code: str,
    name: str,
    result: dict[str, object],
) -> bool:
    runtime_status = str(result.get("runtime_status") or "").strip().upper()
    if runtime_status not in {
        "EARLY_CLOSE",
        "EARLY_CLOSING",
        "EARLY_CLOSED",
        "REVIEW_REQUIRED",
    }:
        return True

    stage = str(result.get("stage") or "").strip()
    notice_by_status = {
        "EARLY_CLOSE": ("EARLY_CLOSE_WAITING", "조기마감 실행 대기"),
        "EARLY_CLOSING": ("EARLY_CLOSE_ORDER_PROGRESS", "조기마감 주문 진행"),
        "EARLY_CLOSED": ("EARLY_CLOSE_COMPLETED", "조기마감 완료"),
        "REVIEW_REQUIRED": ("EARLY_CLOSE_EXECUTION_FAILED", "조기마감 실행 실패"),
    }
    notice, notice_reason = notice_by_status[runtime_status]
    state = read_json_dict(stock_dir / "state.json")
    if (
        str(state.get("status") or "").strip().upper() == runtime_status
        and str(state.get("operation_notice") or "").strip().upper() == notice
    ):
        return True

    metadata: dict[str, object] = {
        "operation_notice": notice,
        "operation_notice_reason": notice_reason,
        "operation_notice_at": now_text(),
    }
    if runtime_status == "REVIEW_REQUIRED":
        metadata.update(
            {
                "review_required": True,
                "review_reason": "EARLY_CLOSE_EXECUTION_FAILED",
            }
        )
    elif runtime_status == "EARLY_CLOSED":
        metadata.update(
            {
                "buy_enabled": False,
                "sell_enabled": False,
            }
        )
    return bool(
        window.update_stock_status(
            stock_dir,
            code,
            name,
            runtime_status,
            metadata,
            f"조기마감/{stage or runtime_status}",
        )
    )


def _start_close_liquidation_execution(
    window,
    *,
    stock_dir: Path,
    code: str,
    name: str,
    method: str,
    command_id: str,
    requested_at: str,
    routine_instance_id: str,
    reason: str,
) -> dict[str, object]:
    """Enter existing Cancel/Candidate/Final-Gate pipelines for one stock."""

    recovery = _production_recovery_gate(
        window,
        code,
        f"{reason}_EXECUTION",
    )
    if recovery is not None and recovery.allowed is not True:
        _log_recovery_block(
            window,
            code=code,
            caller_name=f"{reason}_EXECUTION",
            recovery=recovery,
            routine_instance_id=routine_instance_id,
        )
        return {
            "ok": False,
            "stage": "production_recovery",
            "runtime_status": "UNCHANGED",
            "blocked_reasons": [recovery.reason_code],
        }

    state = read_json_dict(stock_dir / "state.json")
    if _close_liquidation_cancel_required(method):
        cancel_result = (
            window.queue_pending_order_cancellations_for_stock_automatically(
                code,
                routine_instance_id,
                trading_day=_transition_trade_date(
                    requested_at,
                    requested_at,
                ),
                started_at=str(state.get("trade_started_at") or "").strip(),
            )
        )
        if cancel_result.get("ok") is not True:
            return {
                "ok": False,
                "stage": "pending_cancel",
                "runtime_status": "REVIEW_REQUIRED",
                "blocked_reasons": list(
                    cancel_result.get("blocked_reasons") or []
                ),
                "cancel_result": cancel_result,
            }
        if (
            int(cancel_result.get("cancel_requested", 0) or 0) > 0
            or int(cancel_result.get("cancel_pending", 0) or 0) > 0
        ):
            return {
                "ok": True,
                "stage": "awaiting_cancel_confirmation",
                "runtime_status": "EARLY_CLOSE",
                "cancel_result": cancel_result,
            }
        buy_pending, sell_pending = pending_order_side_quantities(
            stock_dir,
            state,
        )
        if buy_pending > 0 or sell_pending > 0:
            return {
                "ok": False,
                "stage": "pending_cancel_evidence",
                "runtime_status": "REVIEW_REQUIRED",
                "blocked_reasons": [
                    "unresolved pending quantity has no confirmed cancel pipeline"
                ],
                "cancel_result": cancel_result,
            }

    direct_method = normalize_direct_liquidation_method(method)
    if not direct_method:
        return {
            "ok": True,
            "stage": "policy_runtime_only",
            "runtime_status": "EARLY_CLOSE",
        }

    queue_snapshot = read_execution_queue_records(ORDER_QUEUE_PATH)
    if queue_snapshot.get("ok") is not True:
        return {
            "ok": False,
            "stage": "queue_read",
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": ["canonical order queue is unavailable"],
        }
    matching_records = [
        record
        for record in queue_snapshot.get("records", ())
        if isinstance(record, dict)
        and str(record.get("source_signal_id") or "").strip() == command_id
    ]
    holding_qty = safe_int_value(state.get("holding_qty"), 0)
    if matching_records:
        return _resume_existing_close_order(
            window,
            matching_records[-1],
            holding_qty=holding_qty,
        )

    if holding_qty <= 0:
        return {
            "ok": True,
            "stage": "completed",
            "runtime_status": "EARLY_CLOSED",
        }

    preview = build_close_liquidation_candidate_preview(
        stock_dir,
        code,
        name,
        direct_method,
        command_id=command_id,
        requested_at=requested_at,
        routine_instance_id=routine_instance_id,
        reason=reason,
    )
    if preview.get("ok") is not True:
        return {
            "ok": False,
            "stage": "candidate_preview",
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": list(preview.get("blocked_reasons") or []),
            "preview": preview,
        }
    commit_result = commit_close_liquidation_candidate_preview(preview)
    if commit_result.get("ok") is not True:
        return {
            "ok": False,
            "stage": str(commit_result.get("stage") or "candidate_commit"),
            "runtime_status": "REVIEW_REQUIRED",
            "blocked_reasons": list(
                commit_result.get("blocked_reasons") or []
            ),
            "preview": preview,
            "commit_result": commit_result,
        }
    result = _close_execution_result(
        window.process_executable_order_for_auto_trade(
            str(commit_result.get("order_id") or "")
        )
    )
    return {
        **result,
        "preview": preview,
        "commit_result": commit_result,
    }


def auto_trade_continue_pending_close_liquidations(
    window,
    *,
    limit: int = 5,
) -> dict[str, object]:
    """Resume Command requests after cancel confirmation without new identity."""

    try:
        from gui_auto_trade_runtime import all_registered_stock_dirs

        stock_dirs = all_registered_stock_dirs()
    except Exception as exc:
        return {
            "processed": 0,
            "blocked": 1,
            "results": [],
            "blocked_reasons": [f"registered stock lookup failed: {exc}"],
        }

    results: list[dict[str, object]] = []
    processed = 0
    blocked_count = 0
    for stock_dir in stock_dirs:
        if len(results) >= max(0, int(limit)):
            break
        stock_path = Path(stock_dir)
        state = read_json_dict(stock_path / "state.json")
        config = read_json_dict(stock_path / "config.json")
        if not state or not config:
            continue
        code = stock_path.name.split("_", 1)[0]
        name = stock_path.name.split("_", 1)[1] if "_" in stock_path.name else ""
        routine_instance_id = str(
            config.get("assigned_routine_instance_id") or ""
        ).strip()

        request = state.get("individual_liquidation_request")
        request = request if isinstance(request, dict) else {}
        if str(request.get("status") or "").strip().upper() == "REQUESTED":
            method = str(request.get("method") or "").strip()
            command_id = str(request.get("command_id") or "").strip()
            requested_at = str(request.get("requested_at") or "").strip()
            reason = "INDIVIDUAL_LIQUIDATION"
        elif str(state.get("early_close_requested_at") or "").strip():
            if str(state.get("status") or "").strip().upper() in {
                "EARLY_CLOSED",
                "REVIEW_REQUIRED",
            }:
                continue
            method = str(state.get("early_close_method") or "").strip()
            command_id = str(state.get("operation_command_id") or "").strip()
            requested_at = str(
                state.get("early_close_requested_at") or ""
            ).strip()
            reason = "EARLY_CLOSE"
        else:
            continue

        recovery = _production_recovery_gate(
            window,
            code,
            f"{reason}_CONTINUE",
        )
        if recovery is not None and recovery.allowed is not True:
            _log_recovery_block(
                window,
                code=code,
                caller_name=f"{reason}_CONTINUE",
                recovery=recovery,
                routine_instance_id=routine_instance_id,
            )
            results.append(
                {
                    "stock_dir": str(stock_path),
                    "code": code,
                    "command_id": command_id,
                    "ok": False,
                    "stage": "production_recovery",
                    "runtime_status": "UNCHANGED",
                    "blocked_reasons": [recovery.reason_code],
                }
            )
            blocked_count += 1
            continue

        result = _start_close_liquidation_execution(
            window,
            stock_dir=stock_path,
            code=code,
            name=name,
            method=method,
            command_id=command_id,
            requested_at=requested_at,
            routine_instance_id=routine_instance_id,
            reason=reason,
        )
        if reason == "EARLY_CLOSE":
            persisted = _persist_early_close_execution_result(
                window,
                stock_dir=stock_path,
                code=code,
                name=name,
                result=result,
            )
            if not persisted:
                result = {
                    **result,
                    "ok": False,
                    "stage": "runtime_state_write",
                    "blocked_reasons": ["early close Runtime state write failed"],
                }
            else:
                result = {
                    **result,
                    "completion_check_result": check_global_close_completion_after_durable_update(
                        source=SOURCE_EARLY_CLOSE_DURABLE_UPDATE,
                    ),
                }
        results.append(
            {
                "stock_dir": str(stock_path),
                "code": code,
                "command_id": command_id,
                **result,
            }
        )
        if result.get("ok") is True:
            processed += 1
        else:
            blocked_count += 1

    return {
        "processed": processed,
        "blocked": blocked_count,
        "results": results,
        "blocked_reasons": [],
    }


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


def auto_trade_apply_selected_individual_liquidation_method(
    window,
    method: str,
    minutes_before_regular_close: str = "5",
) -> None:
    dialog_parent = operation_dialog_parent(window)
    normalized_method = short_close_method_text(method)
    if normalized_method not in {"시장가", "현재가", "이월"}:
        return

    selected = window.selected_stock_infos()
    if not selected:
        return

    minutes = str(minutes_before_regular_close).strip() or "5"
    command_service = OperationCommandService(PROJECT_ROOT)
    completed: list[str] = []
    failed: list[str] = []
    for stock_dir, code, name in selected:
        requested_at = now_text()
        state = read_json_dict(stock_dir / "state.json")
        config = read_json_dict(stock_dir / "config.json")
        routine_instance_id = str(
            config.get("assigned_routine_instance_id") or ""
        ).strip()
        recovery = _production_recovery_gate(
            window,
            code,
            "INDIVIDUAL_LIQUIDATION_REQUEST",
        )
        if recovery is not None and recovery.allowed is not True:
            _log_recovery_block(
                window,
                code=code,
                caller_name="INDIVIDUAL_LIQUIDATION_REQUEST",
                recovery=recovery,
                routine_instance_id=routine_instance_id,
            )
            failed.append(
                f"{code} {name}({_recovery_block_user_message(window, recovery)})"
            )
            continue
        current_policy, _is_override = effective_liquidation_policy_for_config(
            config,
            state,
        )
        current_method = str(current_policy.get("method") or "").strip()
        current_request = state.get("individual_liquidation_request")
        current_request = (
            current_request if isinstance(current_request, dict) else {}
        )
        current_started_at = str(
            current_request.get("requested_at") or requested_at
        ).strip()
        current_command_id = str(
            current_request.get("command_id")
            or current_request.get("operation_command_id")
            or state.get("operation_command_id")
            or ""
        ).strip()
        transition = evaluate_production_transition(
            policy_domain=DOMAIN_LIQUIDATION,
            current_policy=current_method,
            requested_policy=normalized_method,
            queue_path=ORDER_QUEUE_PATH,
            fills_path=FILLS_PATH,
            runtime_state=state,
            runtime_routine_instance_id=routine_instance_id,
            scope=_command_transition_scope(
                code=code,
                routine_instance_id=routine_instance_id,
                started_at=current_started_at,
                requested_at=requested_at,
                operation_command_id=current_command_id,
            ),
        )
        if not transition.allowed:
            failed.append(
                f"{code} {name}(정책 전환 차단:{transition.reason_code})"
            )
            continue
        result = command_service.apply_individual_liquidation(
            OperationCommandRequest(
                target_scope=SCOPE_STOCK,
                target_id=str(stock_dir.resolve()),
                command=COMMAND_INDIVIDUAL_LIQUIDATION,
                source="우클릭",
            ),
            IndividualLiquidationOverride(
                method=normalized_method,
                minutes_before_regular_close=minutes,
            ),
        )
        if (
            result.status == RESULT_SUCCESS
            and result.stock_results
            and result.stock_results[0].status == STOCK_APPLIED
        ):
            saved_state = read_json_dict(stock_dir / "state.json")
            saved_request = saved_state.get("individual_liquidation_request")
            saved_request = (
                saved_request if isinstance(saved_request, dict) else {}
            )
            execution = _start_close_liquidation_execution(
                window,
                stock_dir=stock_dir,
                code=code,
                name=name,
                method=normalized_method,
                command_id=result.command_id,
                requested_at=str(
                    saved_request.get("requested_at") or requested_at
                ).strip(),
                routine_instance_id=routine_instance_id,
                reason="INDIVIDUAL_LIQUIDATION",
            )
            if execution.get("ok") is not True:
                failed.append(
                    f"{code} {name}("
                    f"{execution.get('stage') or '실행 연결 실패'})"
                )
                continue
            completed.append(f"{code} {name}")
            append_stock_log(
                stock_dir,
                "GUI",
                "개별청산 요청: "
                f"{minutes}분/{normalized_method} / "
                f"{execution.get('stage')}",
            )
        else:
            reason = result.error
            if result.stock_results:
                reason = result.stock_results[0].error or reason
            failed.append(f"{code} {name}({reason or '요청 실패'})")

    if not completed:
        if failed:
            QMessageBox.critical(
                dialog_parent,
                "개별청산 요청 오류",
                "개별청산 요청을 Runtime에 반영하지 못했습니다.\n\n"
                + "\n".join(failed),
            )
        return

    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()
    window.refresh_all()
    window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
    window._runtime_file_snapshot = window.current_runtime_file_signature()
    window.update_action_buttons()
    window.statusBarMessage(
        f"개별청산 요청 완료: {minutes}분/{normalized_method} / 대상 {len(completed)}개"
    )
    if failed:
        QMessageBox.warning(
            dialog_parent,
            "개별청산 일부 실패",
            "\n".join(failed),
        )



def auto_trade_apply_selected_early_close_default(window) -> None:
    """외부 조기마감 버튼: 좌측 선택 scope의 현재 등록 종목에 디폴트값을 적용한다."""
    method = str(operation_policy_section("early_close").get("method", "루틴")).strip() or "루틴"
    selected = _early_close_scope_stock_infos(window)
    auto_trade_apply_selected_early_close(
        window,
        method,
        source="디폴트값",
        selected=selected,
    )


def _early_close_scope_stock_infos(window) -> list[tuple[Path, str, str]]:
    result: list[tuple[Path, str, str]] = []
    for stock_dir in _selected_instance_stock_dirs(window):
        code, name = parse_stock_folder_name(stock_dir.name)
        if not code:
            config = read_json_dict(stock_dir / "config.json")
            code = str(config.get("code") or config.get("stock_code") or "").strip()
            name = str(config.get("name") or config.get("stock_name") or "").strip()
        if not code:
            continue
        result.append((stock_dir, code, name))
    return result


def _kiwoom_server_login_block_message(window) -> str:
    try:
        parent = window.parent()
    except Exception:
        parent = None
    api = getattr(parent, "kiwoom_api", None)
    checker = getattr(api, "is_connected", None)
    try:
        connected = callable(checker) and checker() is True
    except Exception:
        connected = False
    return "" if connected else "키움 서버에 로그인되어 있지 않습니다."



def auto_trade_apply_selected_early_close_profit_loss(window) -> None:
    """우클릭 조기마감 > 손/익절: 익절/손절 비율을 분리 입력 후 전환한다."""
    dialog = ProfitLossEarlyCloseDialog(operation_dialog_parent(window))
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
    selected: list[tuple[Path, str, str]] | None = None,
) -> None:
    """선택 종목에 조기마감 명령을 적용한다.

    조기마감은 보유수량을 0으로 만드는 1차 리셋 절차다.
    대상 기준은 보유수량이며, 미수/미도/미체결은 대상 판정 기준으로 쓰지 않는다.
    루틴 방식 조기마감은 첫 매도신호 전까지 매수/매도 신호를 허용하고,
    첫 매도주문 접수 이후 추가 주문 차단은 메인 주문판정 계층에서 처리한다.
    """
    login_block_message = _kiwoom_server_login_block_message(window)
    if login_block_message:
        show_toast(window, login_block_message, duration_ms=2500)
        return

    selected = selected if selected is not None else window.selected_stock_infos()
    routine_name = window.current_selected_routine_name()
    dialog_parent = operation_dialog_parent(window)

    def show_ok_message(icon, title: str, message: str) -> None:
        box = QMessageBox(dialog_parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(message)
        ok_button = box.addButton("확인", QMessageBox.AcceptRole)
        box.setDefaultButton(ok_button)
        box.exec_()

    if not selected:
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
    early_close_applied_count = 0

    for stock_dir, code, name in selected:
        state = read_json_dict(stock_dir / "state.json")
        config = read_json_dict(stock_dir / "config.json")
        if not config:
            config = default_config()

        status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
        if is_emergency_stopped_state(state):
            skipped_preview_items.append(f"{code} {name}({auto_trade_status_display(status)})")
            continue
        if is_review_required_state(state):
            skipped_preview_items.append(f"{code} {name}(검토종목)")
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
    if close_targets or review_items:
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

        box = QMessageBox(dialog_parent)
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
    for stock_dir, code, name in selected:
        state = read_json_dict(stock_dir / "state.json")
        status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
        if is_emergency_stopped_state(state):
            skipped.append(f"{code} {name}({auto_trade_status_display(status)})")
            continue
        if is_review_required_state(state):
            skipped.append(f"{code} {name}(검토종목)")
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
        recovery = _production_recovery_gate(
            window,
            code,
            "EARLY_CLOSE_REQUEST",
        )
        if recovery is not None and recovery.allowed is not True:
            _log_recovery_block(
                window,
                code=code,
                caller_name="EARLY_CLOSE_REQUEST",
                recovery=recovery,
                routine_instance_id=str(
                    config.get("assigned_routine_instance_id") or ""
                ).strip(),
            )
            skipped.append(
                f"{code} {name}({_recovery_block_user_message(window, recovery)})"
            )
            continue
        transition_requested_at = now_text()
        routine_instance_id = str(
            config.get("assigned_routine_instance_id") or ""
        ).strip()
        current_policy, current_started_at = _current_close_transition_policy(
            state,
            transition_requested_at,
        )
        current_command_id = str(state.get("operation_command_id") or "").strip()
        transition = evaluate_production_transition(
            policy_domain=DOMAIN_CLOSE,
            current_policy=current_policy,
            requested_policy=method_text,
            queue_path=ORDER_QUEUE_PATH,
            fills_path=FILLS_PATH,
            runtime_state=state,
            runtime_routine_instance_id=routine_instance_id,
            scope=_command_transition_scope(
                code=code,
                routine_instance_id=routine_instance_id,
                started_at=current_started_at,
                requested_at=transition_requested_at,
                operation_command_id=current_command_id,
            ),
        )
        if not transition.allowed:
            skipped.append(
                f"{code} {name}(정책 전환 차단:{transition.reason_code})"
            )
            continue
        intent_result = apply_close_intent(
            intent=CLOSE_INTENT_EARLY_CLOSE,
            target_scope=SCOPE_STOCK,
            target_id=str(stock_dir.resolve()),
            source=source,
            requested_policy=method_text,
            has_close_progress_quantity=has_close_progress_qty,
            extra_policy=dict(extra_policy or {}),
            stock_code=code,
            runtime_state=state,
            runtime_routine_instance_id=routine_instance_id,
            current_policy=current_policy,
            current_started_at=current_started_at,
            current_command_id=current_command_id,
            requested_at=transition_requested_at,
            project_root=PROJECT_ROOT,
            queue_path=ORDER_QUEUE_PATH,
            fills_path=FILLS_PATH,
            operation_command_service_factory=OperationCommandService,
            transition_guard=evaluate_production_transition,
        )
        command_result = intent_result.get("command_result")
        if command_result is None:
            skipped.append(
                f"{code} {name}({intent_result.get('reason') or '紐낅졊 ?곸슜 ?ㅽ뙣'})"
            )
            continue
        if command_result.status == RESULT_FAILED or command_result.failed:
            reason = command_result.error
            if command_result.failed:
                reason = command_result.failed[0].error or reason
            skipped.append(f"{code} {name}({reason or '명령 적용 실패'})")
            continue

        if not has_close_progress_qty:
            completed.append(f"{code} {name}")
            if (
                command_result.stock_results
                and command_result.stock_results[0].status == STOCK_APPLIED
            ):
                append_stock_log(
                    stock_dir,
                    "GUI",
                    "자동매매 상태 변경: 조기마감 대상 없음",
                )
            continue

        saved_state = read_json_dict(stock_dir / "state.json")
        execution = _start_close_liquidation_execution(
            window,
            stock_dir=stock_dir,
            code=code,
            name=name,
            method=method_text,
            command_id=command_result.command_id,
            requested_at=str(
                saved_state.get("early_close_requested_at")
                or transition_requested_at
            ).strip(),
            routine_instance_id=routine_instance_id,
            reason="EARLY_CLOSE",
        )
        persisted = _persist_early_close_execution_result(
            window,
            stock_dir=stock_dir,
            code=code,
            name=name,
            result=execution,
        )
        if not persisted:
            skipped.append(f"{code} {name}(조기마감 상태 저장 실패)")
            continue
        execution = {
            **execution,
            "completion_check_result": check_global_close_completion_after_durable_update(
                source=SOURCE_EARLY_CLOSE_DURABLE_UPDATE,
            ),
        }
        if execution.get("ok") is not True:
            skipped.append(
                f"{code} {name}("
                f"{execution.get('stage') or '실행 연결 실패'})"
            )
            continue

        completed.append(f"{code} {name}")
        early_close_applied_count += 1
        if command_result.stock_results and command_result.stock_results[0].status == STOCK_APPLIED:
            log_reason = (
                f"조기마감/{method_text}/{execution.get('stage')}"
                if has_close_progress_qty
                else "조기마감 대상 없음"
            )
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
            f"조기마감 상태 변경: {routine_name or '전체'} -> {' | '.join(changelog_parts)}",
        )

    window.refresh_all()
    window.stock_table.viewport().update()
    window.stock_table.repaint()

    message = f"조기마감 적용: {len(completed)}개"
    if skipped:
        message += f" / 제외 {len(skipped)}개"
    window.statusBarMessage(message)
    if early_close_applied_count > 0:
        toast_message = f"{early_close_applied_count}종목을 조기마감 적용하였습니다."
    elif not close_targets:
        toast_message = "조기마감 적용 대상이 없습니다."
    elif skipped:
        toast_message = skipped[0]
    else:
        toast_message = "조기마감 적용 대상이 없습니다."
    show_toast(window, toast_message, duration_ms=2500)
