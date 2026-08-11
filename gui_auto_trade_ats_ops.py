# -*- coding: utf-8 -*-
"""
gui_auto_trade_ats_ops.py

자동매매설정창의 수동운영 ATS 설정 처리 헬퍼.

주의:
- 조기마감/자동마감/청산 정책은 다루지 않는다.
- AutoTradeSettingWindow 본체를 직접 import하지 않고 window 객체를 인자로 받아 동작한다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PyQt5.QtWidgets import QMessageBox
from gui_operation_ui_context import operation_dialog_parent

from gui_auto_trade_runtime import now_text
from gui_ats_utils import (
    manual_ats_active_now,
    manual_ats_session_labels,
)
from gui_auto_trade_policy import (
    auto_trade_setting_trade_started,
)
from order_candidate_engine import get_real_holding_qty
from manual_ats_liquidation_service import (
    build_manual_ats_liquidation_preview,
    commit_manual_ats_liquidation_preview,
    ensure_manual_ats_liquidation_request,
    normalize_manual_ats_sell_method,
)
from operation_command_service import (
    MANUAL_ATS_LIQUIDATION_REQUEST_KEY,
    OperationCommandService,
)
from execution_queue_writer import read_execution_queue_records
from operation_close_completion_evaluator import resolve_liquidation_holding_quantity
from event_journal_trade_observer import observe_manual_ats_liquidation_outcome
from runtime_io import read_json_dict
from manual_ats_runtime import (
    manual_ats_runtime_selected_keys,
    write_manual_ats_runtime_selection,
)
from state_policy import normalize_operation_mode


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
BROKER_HOLDINGS_PATH = PROJECT_ROOT / "runtime" / "broker_holdings.json"


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


def auto_trade_selected_manual_ats_liquidation_available(
    window,
    selected: list[tuple[Path, str, str]] | None = None,
    *,
    now_dt=None,
) -> bool:
    """Return whether at least one selected stock can run ATS liquidation."""
    targets = list(selected) if selected is not None else list(window.selected_stock_infos())
    return any(
        _manual_ats_liquidation_target_eligibility(stock_dir, now_dt=now_dt)[
            "eligible"
        ]
        is True
        for stock_dir, _code, _name in targets
    )


def _manual_ats_liquidation_target_eligibility(
    stock_dir: Path,
    *,
    now_dt=None,
) -> dict[str, object]:
    """Evaluate one stock without borrowing ATS state from other selections."""
    config = read_json_dict(stock_dir / "config.json")
    state = read_json_dict(stock_dir / "state.json")
    selected_sessions = manual_ats_runtime_selected_keys(state, now_dt=now_dt)
    reasons: list[str] = []
    if not auto_trade_setting_trade_started(state):
        reasons.append("auto trade is not running")
    if not manual_ats_active_now(config, state, now_dt):
        reasons.append("current time is outside the selected ATS sessions")
    holding_qty = get_real_holding_qty(state)
    if holding_qty is None or holding_qty <= 0:
        reasons.append("actual holding quantity is missing or zero")
    return {
        "eligible": not reasons,
        "selected_sessions": selected_sessions,
        "blocked_reasons": reasons,
    }


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


def auto_trade_set_selected_manual_ats_flag(window, flag_key: str, enabled: bool, label: str) -> None:
    """우클릭 ATS 서브메뉴에서 한 구간만 기존 Writer로 변경한다."""
    current = window.selected_manual_ats_state()
    current[flag_key] = bool(enabled)
    changed_count = window.save_selected_manual_ats_state(
        current,
        None,
        (flag_key,),
    )
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


def _manual_ats_liquidation_event_details(
    preview: dict[str, object],
    result: dict[str, object],
) -> dict[str, object]:
    stock_dir = Path(str(preview.get("stock_dir") or ""))
    config = read_json_dict(stock_dir / "config.json")
    state = read_json_dict(stock_dir / "state.json")
    request = state.get(MANUAL_ATS_LIQUIDATION_REQUEST_KEY)
    request = request if isinstance(request, dict) else {}
    final_preview = result.get("preview")
    final_preview = final_preview if isinstance(final_preview, dict) else preview
    holding = result.get("holding_result")
    holding = holding if isinstance(holding, dict) else request
    cancel = result.get("cancel_result")
    cancel = cancel if isinstance(cancel, dict) else {}
    identities = request.get("cancel_order_identities")
    identities = identities if isinstance(identities, list) else []
    blocked_reasons = [
        str(value) for value in result.get("blocked_reasons", []) if value
    ]
    return {
        "liquidation_method": str(
            final_preview.get("sell_method")
            or preview.get("sell_method")
            or request.get("sell_method")
            or ""
        ),
        "operation_mode": normalize_operation_mode(config.get("operation_mode", "")),
        "selected_sessions": list(
            final_preview.get("selected_ats_sessions")
            or request.get("selected_ats_sessions")
            or []
        ),
        "active_ats_sessions": list(final_preview.get("active_ats_sessions") or []),
        "trade_started": auto_trade_setting_trade_started(state),
        "initial_holding_qty": request.get(
            "initial_holding_qty",
            preview.get("holding_qty", get_real_holding_qty(state)),
        ),
        "pending_order_count": request.get(
            "pending_order_count",
            len(cancel.get("cancel_order_identities") or identities),
        ),
        "cancel_requested_count": request.get(
            "cancel_requested_count",
            int(cancel.get("cancel_requested", 0) or 0),
        ),
        "cancel_confirmed_count": len(identities),
        "position_qty": holding.get("position_qty"),
        "broker_holding_qty": holding.get("broker_holding_qty"),
        "resolved_liquidation_qty": holding.get("resolved_liquidation_qty"),
        "reconciliation_result": str(holding.get("reconciliation_result") or ""),
        "final_request_status": str(request.get("status") or result.get("result_status") or ""),
        "failure_or_block_reason": blocked_reasons,
        "command_id": str(preview.get("command_id") or request.get("command_id") or ""),
    }


def _observe_manual_ats_liquidation_final(
    preview: dict[str, object],
    result: dict[str, object],
    journal_result: str,
    reason_code: str,
) -> None:
    observe_manual_ats_liquidation_outcome(
        command_id=str(preview.get("command_id") or ""),
        stock_code=str(preview.get("code") or ""),
        stock_name=str(preview.get("name") or ""),
        result=journal_result,
        reason_code=reason_code,
        details=_manual_ats_liquidation_event_details(preview, result),
    )


def _dispatch_manual_ats_liquidation_preview(
    window,
    preview: dict[str, object],
) -> dict[str, object]:
    """Commit and dispatch one already-approved-to-resume stock request."""
    commit_result = commit_manual_ats_liquidation_preview(
        preview,
        project_root=PROJECT_ROOT,
    )
    if commit_result.get("ok") is not True:
        return {
            "ok": False,
            "stage": str(commit_result.get("stage") or "candidate_commit"),
            "preview": preview,
            "commit_result": commit_result,
            "blocked_reasons": list(commit_result.get("blocked_reasons") or []),
        }

    order_id = str(commit_result.get("order_id") or "")
    execution_result = window.process_executable_order_for_auto_trade(order_id)
    result_status = _manual_ats_result_status(execution_result)
    detail = ", ".join(
        str(value)
        for value in execution_result.get("blocked_reasons", [])
        if value
    )
    command_service = OperationCommandService(PROJECT_ROOT)
    status_result = command_service.record_manual_ats_liquidation_status(
        str(preview.get("stock_dir") or ""),
        str(preview.get("command_id") or ""),
        result_status,
        order_id=order_id,
        detail=detail,
    )
    if status_result.status != "APPLIED":
        return {
            "ok": False,
            "stage": "runtime_status_readback",
            "preview": preview,
            "commit_result": commit_result,
            "execution_result": execution_result,
            "blocked_reasons": [
                status_result.error
                or "SendOrder result Runtime read-back failed"
            ],
        }
    return {
        "ok": result_status == "SEND_CALL_ACCEPTED",
        "stage": "send_order",
        "result_status": result_status,
        "order_id": order_id,
        "preview": preview,
        "commit_result": commit_result,
        "execution_result": execution_result,
        "blocked_reasons": [] if result_status == "SEND_CALL_ACCEPTED" else [detail or result_status],
    }


def _finalize_manual_ats_liquidation_with_latest_holding(
    window,
    preview: dict[str, object],
) -> dict[str, object]:
    """Reconcile the latest durable holding, then create at most one sell order."""

    stock_dir = str(preview.get("stock_dir") or "")
    command_id = str(preview.get("command_id") or "")
    holding_result = resolve_liquidation_holding_quantity(
        str(preview.get("code") or ""),
        positions_path=POSITIONS_PATH,
        broker_holdings_path=BROKER_HOLDINGS_PATH,
    )
    command_service = OperationCommandService(PROJECT_ROOT)
    if holding_result.get("ok") is not True:
        detail = ", ".join(
            str(value) for value in holding_result.get("blocked_reasons", []) if value
        )
        command_service.record_manual_ats_liquidation_status(
            stock_dir,
            command_id,
            "FAILED",
            detail=detail or "latest holding reconciliation failed",
            holding_readback=holding_result,
        )
        result = {
            "ok": False,
            "stage": "holding_reconciliation",
            "holding_result": holding_result,
            "blocked_reasons": list(holding_result.get("blocked_reasons") or []),
        }
        _observe_manual_ats_liquidation_final(
            preview,
            result,
            "FAILED",
            "HOLDING_RECONCILIATION_FAILED",
        )
        return result

    quantity = int(holding_result.get("resolved_liquidation_qty") or 0)
    next_status = "COMPLETED" if quantity <= 0 else "READY_TO_RESUME"
    status_result = command_service.record_manual_ats_liquidation_status(
        stock_dir,
        command_id,
        next_status,
        detail="" if quantity > 0 else "latest reconciled holding quantity is zero",
        holding_readback=holding_result,
    )
    if status_result.status != "APPLIED":
        result = {
            "ok": False,
            "stage": "holding_status_readback",
            "holding_result": holding_result,
            "blocked_reasons": [
                status_result.error or "latest holding read-back was not persisted"
            ],
        }
        _observe_manual_ats_liquidation_final(
            preview,
            result,
            "FAILED",
            "HOLDING_STATUS_READBACK_FAILED",
        )
        return result
    if quantity <= 0:
        result = {
            "ok": True,
            "stage": "completed_no_holding",
            "result_status": "COMPLETED",
            "holding_result": holding_result,
            "blocked_reasons": [],
        }
        _observe_manual_ats_liquidation_final(
            preview,
            result,
            "COMPLETED",
            "NO_REMAINING_HOLDING",
        )
        return result

    refreshed = build_manual_ats_liquidation_preview(
        stock_dir,
        str(preview.get("code") or ""),
        str(preview.get("name") or ""),
        tuple(preview.get("selected_ats_sessions") or ()),
        str(preview.get("sell_method") or ""),
        command_id=command_id,
        holding_qty_override=quantity,
    )
    if refreshed.get("ok") is not True:
        detail = ", ".join(
            str(value) for value in refreshed.get("blocked_reasons", []) if value
        )
        command_service.record_manual_ats_liquidation_status(
            stock_dir,
            command_id,
            "FAILED",
            detail=detail or "ATS liquidation preview refresh failed",
            holding_readback=holding_result,
        )
        result = {
            "ok": False,
            "stage": "latest_holding_preview",
            "holding_result": holding_result,
            "preview": refreshed,
            "blocked_reasons": list(refreshed.get("blocked_reasons") or []),
        }
        _observe_manual_ats_liquidation_final(
            preview,
            result,
            "FAILED",
            "FINAL_PREVIEW_FAILED",
        )
        return result
    result = _dispatch_manual_ats_liquidation_preview(window, refreshed)
    _observe_manual_ats_liquidation_final(
        preview,
        result,
        "REQUESTED" if result.get("result_status") == "SEND_CALL_ACCEPTED" else "FAILED",
        "ORDER_REQUESTED" if result.get("result_status") == "SEND_CALL_ACCEPTED" else "ORDER_REQUEST_FAILED",
    )
    return result


def _start_manual_ats_liquidation_with_cancel_boundary(
    window,
    preview: dict[str, object],
) -> dict[str, object]:
    """Persist one request, cancel its pending orders, or dispatch immediately."""
    request_result = ensure_manual_ats_liquidation_request(
        preview,
        project_root=PROJECT_ROOT,
    )
    if request_result.get("ok") is not True:
        reasons = list(request_result.get("blocked_reasons") or [])
        if not any("already waiting" in str(value) for value in reasons):
            _observe_manual_ats_liquidation_final(
                preview,
                request_result,
                "FAILED",
                "REQUEST_PERSIST_FAILED",
            )
        return request_result

    stock_dir = Path(str(preview.get("stock_dir") or ""))
    state = read_json_dict(stock_dir / "state.json")
    config = read_json_dict(stock_dir / "config.json")
    requested_at = str(preview.get("requested_at") or "").strip()
    try:
        trading_day = datetime.fromisoformat(requested_at).date().isoformat()
    except ValueError:
        trading_day = ""
    candidate = preview.get("order_candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    routine_instance_id = str(
        config.get("assigned_routine_instance_id")
        or candidate.get("routine")
        or ""
    ).strip()
    cancel_result = window.queue_pending_order_cancellations_for_stock_automatically(
        str(preview.get("code") or ""),
        routine_instance_id,
        trading_day=trading_day,
        started_at=str(state.get("trade_started_at") or "").strip(),
    )
    command_service = OperationCommandService(PROJECT_ROOT)
    command_id = str(preview.get("command_id") or "")
    if cancel_result.get("ok") is not True:
        detail = ", ".join(
            str(value) for value in cancel_result.get("blocked_reasons", []) if value
        )
        command_service.record_manual_ats_liquidation_status(
            str(stock_dir),
            command_id,
            "FAILED",
            detail=detail or "pending order cancellation failed",
            cancel_readback={
                "initial_holding_qty": preview.get("holding_qty"),
                "pending_order_count": len(cancel_result.get("cancel_order_identities") or []),
                "cancel_requested_count": int(cancel_result.get("cancel_requested", 0) or 0),
                "cancel_pending_count": int(cancel_result.get("cancel_pending", 0) or 0),
            },
        )
        result = {
            "ok": False,
            "stage": "pending_cancel",
            "cancel_result": cancel_result,
            "blocked_reasons": list(cancel_result.get("blocked_reasons") or []),
        }
        _observe_manual_ats_liquidation_final(
            preview,
            result,
            "FAILED",
            "PENDING_CANCEL_FAILED",
        )
        return result

    cancel_count = int(cancel_result.get("cancel_requested", 0) or 0) + int(
        cancel_result.get("cancel_pending", 0) or 0
    )
    if cancel_count > 0:
        identities = list(cancel_result.get("cancel_order_identities") or [])
        status_result = command_service.record_manual_ats_liquidation_status(
            str(stock_dir),
            command_id,
            "WAITING_CANCEL_CONFIRMATION",
            cancel_order_identities=identities,
            cancel_readback={
                "initial_holding_qty": preview.get("holding_qty"),
                "pending_order_count": len(identities),
                "cancel_requested_count": int(cancel_result.get("cancel_requested", 0) or 0),
                "cancel_pending_count": int(cancel_result.get("cancel_pending", 0) or 0),
            },
        )
        if status_result.status != "APPLIED":
            result = {
                "ok": False,
                "stage": "waiting_status_readback",
                "cancel_result": cancel_result,
                "blocked_reasons": [status_result.error or "cancel waiting state was not persisted"],
            }
            _observe_manual_ats_liquidation_final(
                preview,
                result,
                "FAILED",
                "WAITING_STATUS_READBACK_FAILED",
            )
            return result
        return {
            "ok": True,
            "stage": "awaiting_cancel_confirmation",
            "cancel_result": cancel_result,
            "blocked_reasons": [],
        }
    return _finalize_manual_ats_liquidation_with_latest_holding(window, preview)


def _manual_ats_cancel_effects_confirmed(
    request: dict[str, object],
    records: tuple[dict[str, object], ...],
) -> bool:
    identities = request.get("cancel_order_identities")
    identities = identities if isinstance(identities, list) else []
    if not identities:
        return False
    terminal_statuses = {"CANCELLED", "PARTIAL_CANCELLED", "FILLED"}
    for identity in identities:
        if not isinstance(identity, dict):
            return False
        order_queued_id = str(identity.get("order_queued_id") or "").strip()
        matches = [
            record
            for record in records
            if str(record.get("id") or "").strip() == order_queued_id
        ]
        if len(matches) != 1:
            return False
        record = matches[0]
        try:
            remaining = int(record.get("remaining_quantity") or 0)
        except (TypeError, ValueError):
            return False
        if (
            str(record.get("status") or "").strip().upper()
            not in terminal_statuses
            or remaining > 0
        ):
            return False
    return True


def auto_trade_continue_pending_manual_ats_liquidations(
    window,
    *,
    limit: int = 5,
) -> dict[str, object]:
    """Resume durable ATS requests only after Chejan-confirmed cancel effects."""
    from gui_auto_trade_runtime import all_registered_stock_dirs

    queue_snapshot = read_execution_queue_records(ORDER_QUEUE_PATH)
    if queue_snapshot.get("ok") is not True:
        return {"processed": 0, "waiting": 0, "failed": 1, "results": []}
    records = tuple(queue_snapshot.get("records") or ())
    results: list[dict[str, object]] = []
    processed = 0
    waiting = 0
    failed = 0
    for stock_dir in all_registered_stock_dirs():
        if len(results) >= max(0, int(limit)):
            break
        stock_path = Path(stock_dir)
        state = read_json_dict(stock_path / "state.json")
        request = state.get(MANUAL_ATS_LIQUIDATION_REQUEST_KEY)
        request = request if isinstance(request, dict) else {}
        request_status = str(request.get("status") or "").strip().upper()
        if request_status not in {"WAITING_CANCEL_CONFIRMATION", "READY_TO_RESUME"}:
            continue
        if (
            request_status == "WAITING_CANCEL_CONFIRMATION"
            and not _manual_ats_cancel_effects_confirmed(request, records)
        ):
            waiting += 1
            continue

        name = stock_path.name.split("_", 1)[1] if "_" in stock_path.name else ""
        code = stock_path.name.split("_", 1)[0]
        preview = {
            "ok": True,
            "stock_dir": str(stock_path),
            "code": code,
            "name": name,
            "command_id": str(request.get("command_id") or ""),
            "selected_ats_sessions": list(request.get("selected_ats_sessions") or ()),
            "sell_method": str(request.get("sell_method") or ""),
        }
        result = _finalize_manual_ats_liquidation_with_latest_holding(window, preview)
        results.append({"stock_dir": str(stock_path), **result})
        if result.get("ok") is True:
            processed += 1
        else:
            failed += 1
    return {
        "processed": processed,
        "waiting": waiting,
        "failed": failed,
        "results": results,
    }


def auto_trade_execute_selected_manual_ats_liquidation(
    window,
    method: str,
    ats_state: dict[str, bool],
    selected: list[tuple[Path, str, str]] | None = None,
    editable_keys: tuple[str, ...] | None = None,
    selected_sessions: tuple[str, ...] | None = None,
) -> None:
    """현재 선택 ATS 구간에서 수동운영 종목의 일회성 청산을 실행한다."""
    del ats_state, editable_keys, selected_sessions
    selected = list(selected) if selected is not None else list(window.selected_stock_infos())
    dialog_parent = operation_dialog_parent(window)
    if not selected:
        QMessageBox.warning(dialog_parent, "선택 오류", "매도할 수동운영 종목을 선택하세요.")
        return

    previews: list[dict[str, object]] = []
    excluded: list[str] = []
    preview_failed: list[str] = []
    for stock_dir, code, name in selected:
        command_id = uuid4().hex
        eligibility = _manual_ats_liquidation_target_eligibility(stock_dir)
        if eligibility["eligible"] is not True:
            reasons = ", ".join(
                str(value)
                for value in eligibility["blocked_reasons"]
                if value
            )
            excluded.append(f"{code} {name}: {reasons or '청산 대상 아님'}")
            blocked_preview = {
                "stock_dir": str(stock_dir),
                "code": code,
                "name": name,
                "command_id": command_id,
                "sell_method": normalize_manual_ats_sell_method(method),
                "selected_ats_sessions": list(eligibility.get("selected_sessions") or ()),
            }
            _observe_manual_ats_liquidation_final(
                blocked_preview,
                {
                    "ok": False,
                    "stage": "eligibility",
                    "blocked_reasons": list(eligibility.get("blocked_reasons") or []),
                },
                "BLOCKED",
                "ELIGIBILITY_BLOCKED",
            )
            continue
        preview = build_manual_ats_liquidation_preview(
            stock_dir,
            code,
            name,
            eligibility["selected_sessions"],
            method,
            command_id=command_id,
        )
        if preview.get("ok") is not True:
            reasons = ", ".join(str(value) for value in preview.get("blocked_reasons", []) if value)
            preview_failed.append(f"{code} {name}: {reasons or '청산 준비 실패'}")
            _observe_manual_ats_liquidation_final(
                preview,
                {
                    "ok": False,
                    "stage": "initial_preview",
                    "preview": preview,
                    "blocked_reasons": list(preview.get("blocked_reasons") or []),
                },
                "FAILED",
                "INITIAL_PREVIEW_FAILED",
            )
            continue
        previews.append(preview)
    if not previews:
        details = excluded + preview_failed
        QMessageBox.warning(
            dialog_parent,
            f"ATS {method}매도 불가",
            "ATS 청산을 실행할 수 있는 종목이 없습니다.\n\n"
            + "\n".join(details[:10]),
        )
        return

    session_labels = manual_ats_session_labels()
    selected_sessions = tuple(
        dict.fromkeys(
            str(key)
            for preview in previews
            for key in preview.get("selected_ats_sessions", [])
            if key
        )
    )
    selected_label_text = ", ".join(
        str(session_labels.get(key, key)) for key in selected_sessions
    )
    answer = QMessageBox.question(
        dialog_parent,
        f"ATS {method}매도 확인",
        f"선택한 수동운영 종목을 ATS {method} 방식으로 청산 요청하시겠습니까?\n\n"
        f"ATS 구간: {selected_label_text}\n"
        f"대상 종목: {len(previews)}개\n\n"
        "요청은 기존 주문 승인·Queue·Dispatch Claim·SendOrder 안전 경계를 통과합니다.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if answer != QMessageBox.Yes:
        window.statusBarMessage(f"ATS {method}매도 취소")
        return

    completed: list[str] = []
    waiting: list[str] = []
    no_holding: list[str] = []
    failed: list[str] = list(preview_failed)
    for preview in previews:
        code = str(preview.get("code") or "")
        name = str(preview.get("name") or "")
        stock_dir = str(preview.get("stock_dir") or "")
        result = _start_manual_ats_liquidation_with_cancel_boundary(window, preview)
        if result.get("stage") == "awaiting_cancel_confirmation":
            waiting.append(f"{code} {name}")
            continue
        if result.get("ok") is not True:
            reasons = ", ".join(
                str(value) for value in result.get("blocked_reasons", []) if value
            )
            failed.append(f"{code} {name}: {reasons or 'ATS 청산 요청 실패'}")
            continue
        if result.get("stage") == "completed_no_holding":
            no_holding.append(f"{code} {name}")
            continue
        if result.get("result_status") == "SEND_CALL_ACCEPTED":
            completed.append(f"{code} {name}")
            preview_session_text = ", ".join(
                str(session_labels.get(str(key), key))
                for key in preview.get("selected_ats_sessions", [])
                if key
            )
            append_stock_log(
                Path(stock_dir),
                "GUI",
                f"수동운영 ATS {method}매도 SendOrder 접수: {preview_session_text}",
            )
        else:
            failed.append(
                f"{code} {name}: {result.get('result_status') or 'ATS 청산 실패'}"
            )

    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()
    window.refresh_all()
    window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
    window._runtime_file_snapshot = window.current_runtime_file_signature()
    window.update_action_buttons()

    if completed:
        window.statusBarMessage(
            f"ATS {method}매도 SendOrder 접수 기록: {len(completed)}개"
        )
    elif waiting:
        window.statusBarMessage(
            f"ATS {method}매도 취소 확인 대기: {len(waiting)}개"
        )
    elif no_holding:
        window.statusBarMessage(
            f"ATS {method}매도 청산 대상 없음: {len(no_holding)}개"
        )
    if excluded or failed:
        QMessageBox.warning(
            dialog_parent,
            f"ATS {method}매도 결과",
            "일부 종목의 ATS 청산 요청이 제외되었거나 접수되지 않았습니다.\n\n"
            + "\n".join((excluded + failed)[:10]),
        )
