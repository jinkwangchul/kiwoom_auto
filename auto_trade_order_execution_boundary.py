# -*- coding: utf-8 -*-
"""Widget-free automatic-trade order execution production boundary."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from gui_auto_trade_integrity import is_emergency_stopped_state
from gui_auto_trade_policy import (
    auto_trade_setting_close_routine_mode_active,
    auto_trade_setting_close_routine_order_allowed,
)
from gui_auto_trade_runtime import parse_stock_folder_name
from runtime_io import read_json_dict
from state_policy import real_trade_enabled
from operation_policy_gate import read_operation_state
from order_manager import (
    decide_routine_order_for_stock_dir,
    mark_routine_order_accepted_for_stock_dir,
)
from execution_enable_service import commit_execution_enable, preview_execution_enable
from execution_final_send_gate_input_adapter import adapt_final_send_gate_readiness_to_input
from execution_final_send_gate_orchestrator import orchestrate_final_send_gate_preview
from execution_final_send_gate_readiness_policy import evaluate_execution_final_send_gate_readiness
from execution_queue_commit_service import commit_execution_queue_manually
from execution_queue_commit_readiness_policy import evaluate_execution_queue_commit_readiness
from execution_queue_review_to_send_order_preview_adapter import adapt_queue_review_to_send_order_preview
from execution_queue_writer import claim_order_for_dispatch, commit_execution_queue_write
from execution_preview_order_service import preview_execution_for_real_ready_order
from execution_runtime_commit_service import commit_execution_runtime_plan
from execution_runtime_controller import run_execution_runtime_dry_run
from execution_runtime_file_init_approval_gate import approve_execution_runtime_file_init
from execution_runtime_file_init_commit_plan_orchestrator import (
    run_execution_runtime_file_init_commit_plan_orchestrator,
)
from execution_runtime_file_init_commit_service import commit_execution_runtime_file_init_plan
from execution_runtime_file_init_open_policy import evaluate_execution_runtime_file_init_open_policy
from execution_runtime_file_init_preview import build_execution_runtime_file_init_preview
from execution_runtime_real_commit_readiness_policy import evaluate_execution_runtime_real_commit_readiness
from execution_runtime_storage import ExecutionRuntimeStorage
from final_send_gate_service import evaluate_final_send_gate
from kiwoom_send_order_adapter_contract import build_kiwoom_send_order_adapter_contract
from kiwoom_send_order_call_preview import preview_kiwoom_send_order_call
from kiwoom_send_order_executor import execute_claimed_send_order
from kiwoom_screen_allocator import project_order_default_screen_no
from kiwoom_send_order_safety_gate import evaluate_kiwoom_send_order_safety
from order_queued_review_service import review_order_queued_record
from real_order_preflight_service import commit_real_order_preflight, preview_real_order_preflight


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
ORDER_EXECUTIONS_PATH = PROJECT_ROOT / "runtime" / "order_executions.json"
ORDER_LOCKS_PATH = PROJECT_ROOT / "runtime" / "order_locks.json"
ROUTINE_INSTANCE_REQUIRED_MESSAGE = "이 작업을 수행할 대상 루틴을 선택하세요."
CANCEL_SIDE_SCOPE_ALL = "ALL"
CANCEL_SIDE_SCOPE_BUY_ONLY = "BUY_ONLY"

_CANCELABLE_BROKER_OPEN_STATUSES = {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}
_BUY_CANCEL_TERMINAL_STATUSES = {
    "FILLED",
    "CANCELLED",
    "CANCELED",
    "PARTIAL_CANCELLED",
    "BROKER_REJECTED",
    "SEND_CALL_REJECTED",
    "REJECTED",
}
_BUY_CANCEL_UNCERTAIN_STATUSES = {
    "ORDER_QUEUED",
    "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED",
    "SEND_CALL_IN_PROGRESS",
    "SEND_CALL_ACCEPTED",
    "SEND_UNCERTAIN",
}
_ACTIVE_CANCEL_REQUEST_STATUSES = {
    "ORDER_QUEUED",
    "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED",
    "SEND_CALL_IN_PROGRESS",
    "SEND_CALL_ACCEPTED",
    "SEND_UNCERTAIN",
    "BROKER_ACCEPTED",
}


def _queue_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _queue_request_parts(record: Mapping[str, object]) -> tuple[Mapping[str, object], Mapping[str, object]]:
    execution_request = record.get("execution_request")
    execution = execution_request if isinstance(execution_request, Mapping) else {}
    request_preview = execution.get("request_preview")
    preview = request_preview if isinstance(request_preview, Mapping) else {}
    return execution, preview


def _queue_order_account(record: Mapping[str, object]) -> tuple[str, str]:
    execution, preview = _queue_request_parts(record)
    guard_snapshot = execution.get("guard_snapshot")
    guard = guard_snapshot if isinstance(guard_snapshot, Mapping) else {}
    identities = {
        _queue_text(value)
        for value in (
            record.get("account_no"),
            preview.get("account_no"),
            guard.get("account_no"),
        )
        if _queue_text(value)
    }
    if len(identities) > 1:
        return "", "order account identity is inconsistent"
    if not identities:
        return "", "order account identity is unavailable"
    return next(iter(identities)), ""


def _queue_order_code(record: Mapping[str, object]) -> str:
    _execution, preview = _queue_request_parts(record)
    return _queue_text(
        record.get("code") or record.get("stock_code") or preview.get("code")
    ).lstrip("A")


def _queue_order_side(record: Mapping[str, object]) -> str:
    _execution, preview = _queue_request_parts(record)
    return _queue_text(record.get("side") or preview.get("side")).upper()


def _queue_order_action(record: Mapping[str, object]) -> str:
    _execution, preview = _queue_request_parts(record)
    return _queue_text(
        record.get("order_action") or preview.get("order_action") or "NEW"
    ).upper()


def _queue_remaining_quantity(record: Mapping[str, object]) -> tuple[int | None, str]:
    value = record.get("remaining_quantity")
    if value is None or not _queue_text(value):
        return None, "remaining_quantity is required"
    if isinstance(value, bool):
        return None, "remaining_quantity must be a non-negative integer"
    try:
        text = _queue_text(value).replace(",", "")
        quantity = int(text) if text else 0
    except (TypeError, ValueError):
        return None, "remaining_quantity must be a non-negative integer"
    if quantity < 0:
        return None, "remaining_quantity must be a non-negative integer"
    return quantity, ""


def project_buy_only_cancel_readiness(
    order_queue_snapshot: Mapping[str, object] | object,
    *,
    account_no: object,
    stock_code: object,
) -> dict[str, object]:
    """Project whether target BUY pending quantity is durably zero."""

    account = _queue_text(account_no)
    code = _queue_text(stock_code).lstrip("A")
    result: dict[str, object] = {
        "available": False,
        "ready": False,
        "state": "BLOCKED_UNCERTAIN",
        "account_no": account,
        "stock_code": code,
        "pending_buy_order_count": 0,
        "pending_buy_quantity": None,
        "cancel_request_in_progress_count": 0,
        "sell_untouched": True,
        "reason": "",
    }
    if not account or not code:
        result["reason"] = "account_no and stock_code are required"
        return result
    if not isinstance(order_queue_snapshot, Mapping):
        result["reason"] = "order_queue root must be an object"
        return result
    orders = order_queue_snapshot.get("orders")
    if not isinstance(orders, list) or any(not isinstance(item, Mapping) for item in orders):
        result["reason"] = "order_queue orders must be a list of objects"
        return result

    pending_count = 0
    pending_quantity = 0
    active_cancel_count = 0
    source_broker_order_nos: set[str] = set()
    cancel_original_order_nos: list[str] = []
    uncertain: list[str] = []
    for item in orders:
        record = item if isinstance(item, Mapping) else {}
        if _queue_order_code(record) != code:
            continue
        side = _queue_order_side(record)
        action = _queue_order_action(record)
        if side == "SELL":
            continue
        if side != "BUY":
            uncertain.append("target stock order side is unavailable")
            continue
        record_account, account_reason = _queue_order_account(record)
        if account_reason:
            uncertain.append(account_reason)
            continue
        if record_account != account:
            continue
        status = _queue_text(record.get("status")).upper()
        if action == "CANCEL":
            _execution, preview = _queue_request_parts(record)
            original_order_no = _queue_text(
                preview.get("original_order_no")
                or record.get("original_order_no")
            )
            if original_order_no:
                cancel_original_order_nos.append(original_order_no)
            else:
                uncertain.append("cancel request original_order_no is unavailable")
            if (
                status in _ACTIVE_CANCEL_REQUEST_STATUSES
                and record.get("original_order_effect_confirmed") is not True
            ):
                active_cancel_count += 1
            continue
        if action not in {"NEW", "MODIFY"}:
            uncertain.append("target BUY order action is unsupported")
            continue
        broker_order_no = _queue_text(record.get("broker_order_no"))
        if broker_order_no:
            source_broker_order_nos.add(broker_order_no)
        else:
            uncertain.append("target BUY broker_order_no is unavailable")
        remaining, quantity_reason = _queue_remaining_quantity(record)
        if remaining is None:
            uncertain.append(quantity_reason)
            continue
        if status in _CANCELABLE_BROKER_OPEN_STATUSES:
            if remaining > 0:
                pending_count += 1
                pending_quantity += remaining
            continue
        if status in _BUY_CANCEL_TERMINAL_STATUSES:
            if remaining > 0:
                uncertain.append("terminal BUY order has positive remaining_quantity")
            continue
        if status in _BUY_CANCEL_UNCERTAIN_STATUSES or not status:
            uncertain.append("BUY order lifecycle is not broker-confirmed")
            continue
        uncertain.append(f"BUY order status is unsupported: {status}")

    if any(
        original_order_no not in source_broker_order_nos
        for original_order_no in cancel_original_order_nos
    ):
        uncertain.append("cancel request lacks linked original BUY order evidence")

    result["pending_buy_order_count"] = pending_count
    result["pending_buy_quantity"] = pending_quantity if not uncertain else None
    result["cancel_request_in_progress_count"] = active_cancel_count
    if uncertain:
        result["reason"] = "; ".join(dict.fromkeys(uncertain))
        return result
    result["available"] = True
    if pending_quantity > 0:
        result.update(
            {
                "state": "WAITING_BUY_CANCEL",
                "reason": "target BUY pending quantity remains",
            }
        )
        return result
    result.update(
        {
            "ready": True,
            "state": "READY_FOR_LIQUIDATION",
            "reason": "",
        }
    )
    return result


@dataclass(frozen=True)
class AutoTradeOrderExecutionContext:
    """Explicit volatile UI/broker inputs required by the production pipeline."""

    kiwoom_connected: Callable[[], bool]
    account_numbers: Callable[[], list[str]]
    selected_account_no: Callable[[], str]
    send_order_callable: Callable[[], object | None]
    selected_stock_info: Callable[[], tuple[Path, str, str] | None]
    selected_routine_metadata: Callable[[], dict[str, object] | None]
    selected_target_instance_ids: Callable[[], tuple[str, ...]]
    selected_routine_dir: Callable[[], Path | None]
    routine_dirs: Callable[[], list[Path]]
    stock_dirs_in_routine: Callable[[Path], list[Path]]
    base_stocks: Callable[[], list[dict[str, object]]]
    order_queue_path: Callable[[], Path]
    order_executions_path: Callable[[], Path]
    order_locks_path: Callable[[], Path]
    confirm_runtime_file_init: Callable[[Path, Path], bool] | None = None


class AutoTradeOrderExecutionBoundary:
    """Own the existing Queue/Approval/Gate/SendOrder production flow."""

    def __init__(self, context: AutoTradeOrderExecutionContext) -> None:
        self._context = context

    def current_selected_routine_row_metadata(self) -> dict[str, object] | None:
        try:
            value = self._context.selected_routine_metadata()
        except Exception:
            return None
        return dict(value) if isinstance(value, dict) else None

    def current_selected_target_instance_ids(self) -> tuple[str, ...]:
        try:
            return tuple(self._context.selected_target_instance_ids())
        except Exception:
            return ()

    def current_selected_routine_dir(self) -> Path | None:
        try:
            value = self._context.selected_routine_dir()
        except Exception:
            return None
        return value if isinstance(value, Path) else None

    def selected_stock_info(self) -> tuple[Path, str, str] | None:
        try:
            value = self._context.selected_stock_info()
        except Exception:
            return None
        return value if isinstance(value, tuple) and len(value) == 3 else None

    def confirm_execution_runtime_file_init(
        self,
        *,
        order_executions_path: Path,
        order_locks_path: Path,
    ) -> bool:
        callback = self._context.confirm_runtime_file_init
        if callback is None:
            return False
        try:
            return bool(callback(order_executions_path, order_locks_path))
        except Exception:
            return False

    def read_order_from_queue_by_id(self, order_id: str, queue_path: Path) -> dict[str, object]:
        try:
            data = json.loads(queue_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "ok": False,
                "stage": "EXECUTION_ENABLE_ORDER_READ",
                "order": None,
                "blocked_reasons": [f"failed to read order_queue json: {exc}"],
            }

        if not isinstance(data, dict):
            return {
                "ok": False,
                "stage": "EXECUTION_ENABLE_ORDER_READ",
                "order": None,
                "blocked_reasons": ["order_queue root must be an object"],
            }

        orders = data.get("orders")
        if not isinstance(orders, list):
            return {
                "ok": False,
                "stage": "EXECUTION_ENABLE_ORDER_READ",
                "order": None,
                "blocked_reasons": ["order_queue orders must be a list"],
            }

        for item in orders:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "") or "").strip() == order_id:
                return {
                    "ok": True,
                    "stage": "EXECUTION_ENABLE_ORDER_READ",
                    "order": dict(item),
                    "blocked_reasons": [],
                }

        return {
            "ok": False,
            "stage": "EXECUTION_ENABLE_ORDER_READ",
            "order": None,
            "blocked_reasons": ["order_id not found"],
        }


    def real_preflight_stock_config_for_order(self, order: dict[str, object]) -> tuple[dict[str, object], str]:
        code = str(order.get("code", "") or "").strip()
        if not code:
            return {}, "missing_order_code"

        selected_getter = getattr(self, "selected_stock_info", None)
        if callable(selected_getter):
            try:
                selected = selected_getter()
            except Exception:
                selected = None
            if selected is not None:
                stock_dir, selected_code, _selected_name = selected
                if str(selected_code or "").strip() == code:
                    config = read_json_dict(Path(stock_dir) / "config.json")
                    return (config if isinstance(config, dict) else {}, str(stock_dir))

        for routine_dir in self._context.routine_dirs():
            for stock_dir in self._context.stock_dirs_in_routine(routine_dir):
                stock_code, _stock_name = parse_stock_folder_name(Path(stock_dir).name)
                if stock_code != code:
                    continue
                config = read_json_dict(Path(stock_dir) / "config.json")
                return (config if isinstance(config, dict) else {}, str(stock_dir))

        return {}, "stock_config_not_found"

    def build_real_preflight_guard_from_gui(
        self,
        order: dict[str, object],
        *,
        operator_confirmed: bool = False,
    ) -> dict[str, object]:
        try:
            connected = bool(self._context.kiwoom_connected())
        except Exception:
            connected = False
        try:
            account_no = str(self._context.selected_account_no() or "").strip()
        except Exception:
            account_no = ""
        try:
            raw_accounts = self._context.account_numbers()
        except Exception:
            raw_accounts = []
        accounts = [
            str(value or "").strip()
            for value in raw_accounts
            if str(value or "").strip()
        ] if isinstance(raw_accounts, list) else []

        stock_config, stock_config_source = self.real_preflight_stock_config_for_order(order)
        stock_config_found = stock_config_source not in {"missing_order_code", "stock_config_not_found"}
        real_enabled = bool(stock_config_found and real_trade_enabled(stock_config))
        account_selected = bool(account_no and account_no in accounts)

        return {
            "real_trade_enabled": real_enabled,
            "kiwoom_logged_in": connected,
            "account_selected": account_selected,
            "account_no": account_no if account_selected else "",
            "operator_confirmed": bool(operator_confirmed),
            "account_numbers": accounts,
            "selected_account_valid": account_selected,
            "real_trade_source": stock_config_source,
            "real_trade_config_found": stock_config_found,
            "real_trade_guard_source": "gui_session",
        }

    def real_preflight_guard_block_reasons(
        self,
        guard: dict[str, object],
        *,
        include_operator: bool,
    ) -> list[str]:
        reasons: list[str] = []
        if guard.get("kiwoom_logged_in") is not True:
            reasons.append("kiwoom api is not connected")
        accounts = guard.get("account_numbers")
        if not isinstance(accounts, list) or not accounts:
            reasons.append("kiwoom account list is empty")
        if guard.get("account_selected") is not True:
            reasons.append("selected account is missing or stale")
        if guard.get("real_trade_config_found") is not True:
            reasons.append("real trade config for order is not found")
        elif guard.get("real_trade_enabled") is not True:
            reasons.append("real trade is disabled for order stock")
        if include_operator and guard.get("operator_confirmed") is not True:
            reasons.append("operator confirmation is required")
        return reasons

    def execution_runtime_environment_flags(
        self,
        order: dict[str, object] | None = None,
        guard: dict[str, object] | None = None,
        *,
        order_executions_path: Path | None = None,
        order_locks_path: Path | None = None,
    ) -> dict[str, object]:
        order_executions_path = order_executions_path or self._context.order_executions_path()
        order_locks_path = order_locks_path or self._context.order_locks_path()
        order_dict = order if isinstance(order, dict) else {}
        guard_dict = guard if isinstance(guard, dict) else {}
        try:
            canonical_executions = order_executions_path.resolve() == self._context.order_executions_path().resolve()
            canonical_locks = order_locks_path.resolve() == self._context.order_locks_path().resolve()
        except Exception:
            canonical_executions = False
            canonical_locks = False

        issues: list[str] = []
        if guard_dict.get("kiwoom_logged_in") is not True:
            issues.append("kiwoom api is not connected")
        if guard_dict.get("account_selected") is not True or not str(guard_dict.get("account_no") or "").strip():
            issues.append("selected account is missing or stale")
        if guard_dict.get("real_trade_enabled") is not True:
            issues.append("real trade is disabled for order stock")
        if not canonical_executions or not canonical_locks:
            issues.append("runtime target is not the canonical project runtime path")
        if not str(order_dict.get("id") or "").strip():
            issues.append("order id is missing")

        allowed = not issues
        return {
            "real_runtime_file_init_enabled": allowed,
            "allow_project_runtime_file_init": allowed,
            "real_runtime_commit_enabled": allowed,
            "allow_project_runtime_commit": allowed,
            "source": "gui_real_preflight_guard",
            "order_id": str(order_dict.get("id") or "").strip(),
            "account_no": str(guard_dict.get("account_no") or "").strip(),
            "canonical_order_executions_path": canonical_executions,
            "canonical_order_locks_path": canonical_locks,
            "issues": issues,
        }

    def ensure_execution_runtime_files_ready(
        self,
        *,
        order: dict[str, object] | None = None,
        guard: dict[str, object] | None = None,
        order_executions_path: Path | None = None,
        order_locks_path: Path | None = None,
        require_runtime_file_init_dialog: bool = True,
    ) -> dict[str, object]:
        order_executions_path = order_executions_path or self._context.order_executions_path()
        order_locks_path = order_locks_path or self._context.order_locks_path()
        executions_exists = order_executions_path.exists()
        locks_exists = order_locks_path.exists()
        if executions_exists and locks_exists:
            storage = ExecutionRuntimeStorage(order_executions_path, order_locks_path)
            read_result = storage.read()
            if read_result.get("ok") is True:
                return {
                    "runtime_files_ready": True,
                    "runtime_file_init_required": False,
                    "runtime_file_init_result": None,
                    "blocked_reasons": [],
                }
            return {
                "runtime_files_ready": False,
                "runtime_file_init_required": False,
                "runtime_file_init_result": None,
                "blocked_reasons": list(read_result.get("issues") or ["runtime files are invalid"]),
            }

        environment_flags = self.execution_runtime_environment_flags(
            order,
            guard,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
        )
        allow_project_runtime_path = environment_flags.get("allow_project_runtime_file_init") is True
        file_init_preview = build_execution_runtime_file_init_preview(
            order_executions_path,
            order_locks_path,
            allow_project_runtime_path=allow_project_runtime_path,
        )
        if file_init_preview.get("status") != "READY":
            return {
                "runtime_files_ready": False,
                "runtime_file_init_required": file_init_preview.get("status") == "READY",
                "runtime_file_init_preview": file_init_preview,
                "runtime_file_init_result": None,
                "runtime_environment_flags": environment_flags,
                "blocked_reasons": list(file_init_preview.get("issues") or ["runtime file init preview is not ready"]),
            }

        if require_runtime_file_init_dialog and not self.confirm_execution_runtime_file_init(
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
        ):
            return {
                "runtime_files_ready": False,
                "runtime_file_init_required": True,
                "runtime_file_init_preview": file_init_preview,
                "runtime_file_init_result": None,
                "runtime_environment_flags": environment_flags,
                "blocked_reasons": ["runtime file initialization cancelled by operator"],
            }

        approval = approve_execution_runtime_file_init(
            file_init_preview,
            manual_runtime_file_init_confirmed=True,
            manual_project_runtime_path_confirmed=True,
        )
        orchestrator = run_execution_runtime_file_init_commit_plan_orchestrator(
            file_init_preview,
            approval,
        )
        open_policy = evaluate_execution_runtime_file_init_open_policy(
            file_init_commit_plan_orchestrator_result=orchestrator,
            confirmations={
                "manual_runtime_file_init_commit_confirmed": True,
                "manual_project_runtime_path_confirmed": True,
            },
            environment_flags=environment_flags,
        )
        if open_policy.get("status") != "READY_TO_OPEN_FILE_INIT" or open_policy.get("file_init_allowed") is not True:
            return {
                "runtime_files_ready": False,
                "runtime_file_init_required": True,
                "runtime_file_init_preview": file_init_preview,
                "runtime_file_init_approval_gate_result": approval,
                "runtime_file_init_commit_plan_orchestrator_result": orchestrator,
                "runtime_file_init_open_policy_result": open_policy,
                "runtime_file_init_result": None,
                "runtime_environment_flags": environment_flags,
                "blocked_reasons": list(open_policy.get("issues") or ["runtime file init open policy is not ready"]),
            }
        result = commit_execution_runtime_file_init_plan(
            orchestrator,
            manual_runtime_file_init_commit_confirmed=True,
            manual_temp_file_init_confirmed=True,
            file_init_open_policy_result=open_policy,
            manual_project_runtime_file_init_commit_confirmed=True,
        )
        ready = (
            result.get("status") == "COMMITTED"
            and result.get("committed") is True
            and result.get("read_back_verified") is True
        )
        return {
            "runtime_files_ready": ready,
            "runtime_file_init_required": True,
            "runtime_file_init_preview": file_init_preview,
            "runtime_file_init_approval_gate_result": approval,
            "runtime_file_init_commit_plan_orchestrator_result": orchestrator,
            "runtime_file_init_open_policy_result": open_policy,
            "runtime_file_init_result": result,
            "runtime_environment_flags": environment_flags,
            "blocked_reasons": [] if ready else list(result.get("issues") or ["runtime file initialization failed"]),
        }

    def commit_execution_runtime_for_preview(
        self,
        order: dict[str, object],
        guard: dict[str, object],
        execution_preview_result: dict[str, object],
        *,
        order_executions_path: Path | None = None,
        order_locks_path: Path | None = None,
        require_runtime_file_init_dialog: bool = True,
    ) -> dict[str, object]:
        order_executions_path = order_executions_path or self._context.order_executions_path()
        order_locks_path = order_locks_path or self._context.order_locks_path()
        del execution_preview_result
        runtime_files = self.ensure_execution_runtime_files_ready(
            order=order,
            guard=guard,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
            require_runtime_file_init_dialog=require_runtime_file_init_dialog,
        )
        if runtime_files.get("runtime_files_ready") is not True:
            return {
                "runtime_commit_ready": False,
                "runtime_commit_stage": "runtime_file_init",
                "runtime_commit_result": None,
                "runtime_file_init": runtime_files,
                "blocked_reasons": list(runtime_files.get("blocked_reasons") or ["runtime files are not ready"]),
            }

        confirmations = {
            "manual_execution_runtime_commit_confirmed": True,
            "manual_runtime_file_write_confirmed": True,
        }
        environment_flags = self.execution_runtime_environment_flags(
            order,
            guard,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
        )
        storage = ExecutionRuntimeStorage(order_executions_path, order_locks_path)
        runtime_dry_run = run_execution_runtime_dry_run(
            order,
            guard,
            storage,
            confirmations=confirmations,
        )
        commit_plan = runtime_dry_run.get("commit_plan") if isinstance(runtime_dry_run, dict) else None
        if not isinstance(commit_plan, dict) or runtime_dry_run.get("status") != "READY":
            return {
                "runtime_commit_ready": False,
                "runtime_commit_stage": "runtime_commit_plan",
                "runtime_commit_result": None,
                "runtime_file_init": runtime_files,
                "runtime_dry_run_result": runtime_dry_run,
                "commit_plan_orchestrator_result": commit_plan,
                "blocked_reasons": list(runtime_dry_run.get("issues") or ["runtime commit plan is not ready"])
                if isinstance(runtime_dry_run, dict)
                else ["runtime commit plan is malformed"],
            }

        real_commit_readiness = evaluate_execution_runtime_real_commit_readiness(
            runtime_api_result=runtime_dry_run,
            commit_plan_orchestrator_result=commit_plan,
            order_executions_path=order_executions_path,
            order_locks_path=order_locks_path,
            confirmations=confirmations,
            environment_flags=environment_flags,
        )
        if real_commit_readiness.get("status") != "READY_TO_OPEN_RUNTIME_COMMIT":
            return {
                "runtime_commit_ready": False,
                "runtime_commit_stage": "runtime_real_commit_readiness",
                "runtime_commit_result": None,
                "runtime_file_init": runtime_files,
                "runtime_dry_run_result": runtime_dry_run,
                "commit_plan_orchestrator_result": commit_plan,
                "runtime_commit_readiness_policy_result": real_commit_readiness,
                "runtime_environment_flags": environment_flags,
                "blocked_reasons": list(real_commit_readiness.get("issues") or ["runtime real commit readiness is not ready"]),
            }

        runtime_commit_result = commit_execution_runtime_plan(
            commit_plan,
            order_executions_path,
            order_locks_path,
            context=confirmations,
            real_commit_readiness_policy_result=real_commit_readiness,
            manual_project_runtime_commit_confirmed=True,
        )
        required_identity = ("execution_id", "order_id", "request_hash", "lock_id")
        missing_identity = [
            field for field in required_identity if not str(runtime_commit_result.get(field) or "").strip()
        ]
        invalid_reasons: list[str] = []
        if runtime_commit_result.get("status") != "COMMITTED":
            invalid_reasons.append("runtime commit status is not COMMITTED")
        if runtime_commit_result.get("committed") is not True:
            invalid_reasons.append("runtime committed flag is not true")
        if runtime_commit_result.get("runtime_write") is not True:
            invalid_reasons.append("runtime_write flag is not true")
        if runtime_commit_result.get("read_back_verified") is not True:
            invalid_reasons.append("runtime read-back is not verified")
        invalid_reasons.extend(f"missing runtime commit identity: {field}" for field in missing_identity)

        return {
            "runtime_commit_ready": not invalid_reasons,
            "runtime_commit_stage": "runtime_committed" if not invalid_reasons else "runtime_commit_validation",
            "runtime_commit_result": runtime_commit_result,
            "runtime_file_init": runtime_files,
            "runtime_dry_run_result": runtime_dry_run,
            "commit_plan_orchestrator_result": commit_plan,
            "runtime_commit_readiness_policy_result": real_commit_readiness,
            "runtime_environment_flags": environment_flags,
            "blocked_reasons": invalid_reasons,
        }

    @staticmethod
    def queue_file_snapshot(queue_path: Path) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "path": str(queue_path),
            "sha256": None,
            "size": None,
            "mtime": None,
            "orders_count": None,
            "error": None,
        }
        try:
            data = queue_path.read_bytes()
            stat = queue_path.stat()
            snapshot["sha256"] = hashlib.sha256(data).hexdigest().upper()
            snapshot["size"] = stat.st_size
            snapshot["mtime"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            try:
                decoded = json.loads(data.decode("utf-8"))
                orders = decoded.get("orders") if isinstance(decoded, dict) else None
                snapshot["orders_count"] = len(orders) if isinstance(orders, list) else None
                snapshot["revision"] = decoded.get("revision") if isinstance(decoded, dict) else None
            except Exception as exc:
                snapshot["error"] = f"orders_count unavailable: {exc}"
        except Exception as exc:
            snapshot["error"] = str(exc)

        return snapshot

    def verify_manual_queue_commit_read_back(
        self,
        *,
        queue_path: Path,
        queue_write_preview_result: dict[str, object],
        runtime_commit_result: dict[str, object],
    ) -> dict[str, object]:
        record = queue_write_preview_result.get("order_queued_record_preview")
        record_dict = record if isinstance(record, dict) else {}
        expected = {
            "id": str(record_dict.get("id") or "").strip(),
            "order_id": str(record_dict.get("order_id") or "").strip(),
            "execution_id": str(record_dict.get("execution_id") or "").strip(),
            "request_hash": str(record_dict.get("request_hash") or "").strip(),
            "lock_id": str(record_dict.get("lock_id") or "").strip(),
        }
        runtime_expected = {
            "order_id": str(runtime_commit_result.get("order_id") or "").strip(),
            "execution_id": str(runtime_commit_result.get("execution_id") or "").strip(),
            "request_hash": str(runtime_commit_result.get("request_hash") or "").strip(),
            "lock_id": str(runtime_commit_result.get("lock_id") or "").strip(),
        }

        issues: list[str] = []
        for field in ("order_id", "execution_id", "request_hash", "lock_id"):
            if not expected[field] or expected[field] != runtime_expected[field]:
                issues.append(f"runtime/queue identity mismatch before read-back: {field}")
        if not expected["id"]:
            issues.append("order queued record id is missing")
        if issues:
            return {"verified": False, "stage": "identity_precheck", "record": None, "issues": issues}

        try:
            data = json.loads(queue_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "verified": False,
                "stage": "queue_read",
                "record": None,
                "issues": [f"failed to read queue after commit: {exc}"],
            }
        orders = data.get("orders") if isinstance(data, dict) else None
        if not isinstance(orders, list):
            return {
                "verified": False,
                "stage": "queue_structure",
                "record": None,
                "issues": ["queue orders must be a list after commit"],
            }

        matches = [
            item for item in orders
            if isinstance(item, dict) and str(item.get("id") or "").strip() == expected["id"]
        ]
        if len(matches) != 1:
            return {
                "verified": False,
                "stage": "record_count",
                "record": None,
                "issues": [f"expected exactly one ORDER_QUEUED record after commit, found {len(matches)}"],
            }

        actual = dict(matches[0])
        for field in ("order_id", "execution_id", "request_hash", "lock_id"):
            if str(actual.get(field) or "").strip() != expected[field]:
                issues.append(f"read-back identity mismatch: {field}")
        if actual.get("status") != "ORDER_QUEUED":
            issues.append("read-back status is not ORDER_QUEUED")
        if actual.get("send_order_called") is True:
            issues.append("read-back send_order_called is true")
        if actual.get("broker_api_called") is True:
            issues.append("read-back broker_api_called is true")
        if actual.get("actual_order_sent") is True:
            issues.append("read-back actual_order_sent is true")

        return {
            "verified": not issues,
            "stage": "verified" if not issues else "record_validation",
            "record": actual,
            "issues": issues,
        }


    def build_manual_send_order_environment(self, order: dict[str, object], queue_path: Path) -> dict[str, object]:
        try:
            selected_account = str(self._context.selected_account_no() or "").strip()
        except Exception:
            selected_account = ""
        try:
            connected = bool(self._context.kiwoom_connected())
        except Exception:
            connected = False
        try:
            raw_accounts = self._context.account_numbers()
        except Exception:
            raw_accounts = []
        accounts = [
            str(item or "").strip()
            for item in raw_accounts
            if str(item or "").strip()
        ] if isinstance(raw_accounts, list) else []
        try:
            send_order_callable = self._context.send_order_callable()
        except Exception:
            send_order_callable = None

        execution_request = order.get("execution_request")
        execution_request_dict = execution_request if isinstance(execution_request, dict) else {}
        request_preview = execution_request_dict.get("request_preview")
        request_preview_dict = request_preview if isinstance(request_preview, dict) else {}
        order_account = str(order.get("account_no") or "").strip()
        request_account = str(request_preview_dict.get("account_no") or "").strip()

        config, _source = self.real_preflight_stock_config_for_order(order)
        real_trade_enabled = bool(isinstance(config, dict) and config.get("real_trade_enabled") is True)
        try:
            canonical_queue = queue_path.resolve() == self._context.order_queue_path().resolve()
        except Exception:
            canonical_queue = False

        issues: list[str] = []
        if not callable(send_order_callable):
            issues.append("kiwoom api SendOrder callable is unavailable")
        if not connected:
            issues.append("kiwoom api is not connected")
        if not selected_account:
            issues.append("selected account is missing")
        if selected_account and accounts and selected_account not in accounts:
            issues.append("selected account is not in current Kiwoom account list")
        if not order_account:
            issues.append("ORDER_QUEUED account_no is required")
        if not request_account:
            issues.append("execution_request.request_preview.account_no is required")
        if order_account and request_account and order_account != request_account:
            issues.append("ORDER_QUEUED account_no does not match execution request account_no")
        if request_account and selected_account and request_account != selected_account:
            issues.append("selected account does not match execution request account")
        if order_account and selected_account and order_account != selected_account:
            issues.append("selected account does not match ORDER_QUEUED account")
        if not real_trade_enabled:
            issues.append("real trade is disabled for order stock")
        if not canonical_queue:
            issues.append("queue path is not canonical runtime/order_queue.json")

        return {
            "send_order_environment_ready": not issues,
            "issues": issues,
            "kiwoom_connected": connected,
            "selected_account_no": selected_account,
            "order_account_no": order_account,
            "request_account_no": request_account,
            "real_trade_enabled": real_trade_enabled,
            "canonical_queue_path": canonical_queue,
            "send_order_callable": send_order_callable,
        }

    def send_order_identity_from_record(self, record: dict[str, object]) -> dict[str, object]:
        return {
            "order_queued_id": str(record.get("id") or record.get("order_queued_id") or "").strip(),
            "source_signal_id": str(record.get("source_signal_id") or "").strip(),
            "order_id": str(record.get("order_id") or "").strip(),
            "candidate_id": str(record.get("candidate_id") or "").strip(),
            "queue_pending_id": str(record.get("queue_pending_id") or "").strip(),
            "execution_id": str(record.get("execution_id") or "").strip(),
            "request_hash": str(record.get("request_hash") or "").strip(),
            "lock_id": str(record.get("lock_id") or "").strip(),
        }

    def build_manual_send_order_call_preview(
        self,
        order: dict[str, object],
        environment: dict[str, object],
        *,
        operator_confirmed: bool,
    ) -> dict[str, object]:
        execution_request = order.get("execution_request")
        execution_request_dict = execution_request if isinstance(execution_request, dict) else {}
        request_preview = execution_request_dict.get("request_preview")
        request_preview_dict = request_preview if isinstance(request_preview, dict) else {}
        side = str(request_preview_dict.get("side") or order.get("side") or "").strip().upper()
        hoga = str(
            request_preview_dict.get("hoga")
            or request_preview_dict.get("order_type")
            or order.get("hoga")
            or order.get("order_type")
            or ""
        ).strip().upper()
        price = request_preview_dict.get("price", order.get("price", 0))
        quantity = request_preview_dict.get("quantity", order.get("quantity", 0))
        account_no = str(request_preview_dict.get("account_no") or "").strip()
        screen_no = str(
            request_preview_dict.get("screen_no")
            or project_order_default_screen_no()
        ).strip()

        broker_dispatch_preview = {
            "status": "BROKER_DISPATCH_READY",
            "send_order_called": False,
            "broker_called": False,
            "send_order_params_preview": {
                "broker_type": "KIWOOM",
                "dispatch_id": str(order.get("id") or "").strip(),
                "order_id": str(order.get("order_id") or order.get("id") or "").strip(),
                "account_no": account_no,
                "screen_no": screen_no,
                "side": side,
                "order_action": str(request_preview_dict.get("order_action") or request_preview_dict.get("action") or "NEW").strip().upper(),
                "code": str(request_preview_dict.get("code") or order.get("code") or "").strip(),
                "quantity": quantity,
                "price": price,
                "hoga": hoga,
                "original_order_no": str(request_preview_dict.get("original_order_no") or "").strip(),
            },
        }
        adapter_contract = build_kiwoom_send_order_adapter_contract(
            broker_dispatch_preview,
            {"account_no": account_no},
            {"screen_no": screen_no},
        )
        safety = evaluate_kiwoom_send_order_safety(
            adapter_contract,
            {},
            {"connected": environment.get("kiwoom_connected"), "account_no": account_no},
            {"manual_kiwoom_send_order_confirmed": operator_confirmed is True, "emergency_stop": False},
        )
        call_preview = preview_kiwoom_send_order_call(
            safety,
            adapter_contract,
            {"final_call_token": f"GUI_SEND_{uuid4().hex}"},
        )
        call_preview["adapter_contract_result"] = adapter_contract
        call_preview["safety_gate_result"] = safety
        return call_preview

    def build_manual_final_send_gate_result(
        self,
        order: dict[str, object],
        environment: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
        current_queue_snapshot: dict[str, object],
    ) -> dict[str, object]:
        record_review = review_order_queued_record(order)
        if record_review.get("review_ok") is not True:
            return {
                "final_send_gate_ok": False,
                "send_gate_stage": "order_queued_record_review",
                "blocked_reasons": list(record_review.get("blocked_reasons") or ["ORDER_QUEUED review failed"]),
                "order_queued_record_review_result": record_review,
            }

        identity = self.send_order_identity_from_record(order)
        queue_committed_review = {
            "review_type": "EXECUTION_QUEUE_COMMITTED_REVIEW",
            "status": "READY_FOR_FINAL_SEND_GATE",
            "next_stage": "FINAL_SEND_GATE_REQUIRED",
            "preview_only": True,
            "queue_write": False,
            "runtime_write": False,
            "send_order_called": False,
            "order_queued_record": order,
            "identity": {
                "order_id": identity.get("order_id"),
                "source_signal_id": identity.get("source_signal_id"),
                "execution_id": identity.get("execution_id"),
                "request_hash": identity.get("request_hash"),
                "lock_id": identity.get("lock_id"),
            },
            "issues": [],
            "warnings": [],
        }
        adapter_result = adapt_queue_review_to_send_order_preview(queue_committed_review)
        if adapter_result.get("status") != "READY_FOR_FINAL_SEND_GATE":
            return {
                "final_send_gate_ok": False,
                "send_gate_stage": "send_order_preview_adapter",
                "blocked_reasons": list(adapter_result.get("issues") or ["SendOrder preview adapter blocked"]),
                "send_order_preview_adapter_result": adapter_result,
            }

        guard = {
            "real_trade_enabled": environment.get("real_trade_enabled") is True,
            "kiwoom_logged_in": environment.get("kiwoom_connected") is True,
            "account_selected": bool(str(environment.get("selected_account_no") or "").strip()),
            "account_no": str(environment.get("selected_account_no") or "").strip(),
            "operator_confirmed": True,
        }
        final_context = {
            "manual_final_send_confirmed": True,
            "queue_path": str(queue_path),
            "queue_snapshot_hash": queue_snapshot.get("sha256"),
        }
        readiness = evaluate_execution_final_send_gate_readiness(adapter_result, guard, context=final_context)
        input_adapter = adapt_final_send_gate_readiness_to_input(readiness, guard, context=final_context)
        orchestrator = orchestrate_final_send_gate_preview(input_adapter)
        if orchestrator.get("status") != "READY_FOR_FINAL_SEND_GATE" or orchestrator.get("final_send_gate_ready") is not True:
            return {
                "final_send_gate_ok": False,
                "send_gate_stage": "final_send_gate_orchestrator",
                "blocked_reasons": list(orchestrator.get("issues") or ["Final Send Gate orchestrator blocked"]),
                "final_send_gate_readiness_result": readiness,
                "final_send_gate_input_adapter_result": input_adapter,
                "final_send_gate_orchestrator_result": orchestrator,
            }

        final_input = orchestrator.get("final_send_gate_input")
        final_input_dict = final_input if isinstance(final_input, dict) else {}
        final_gate = evaluate_final_send_gate(
            final_input_dict.get("adapter_preview_result"),
            final_input_dict.get("order_queued_record"),
            final_input_dict.get("current_guard"),
            queue_snapshot=queue_snapshot,
            current_queue_snapshot=current_queue_snapshot,
            context=final_input_dict.get("context"),
        )
        final_gate["final_send_gate_result_type"] = "FINAL_SEND_GATE_SERVICE"
        final_gate["queue_path"] = str(queue_path)
        final_gate["queue_revision"] = current_queue_snapshot.get("revision")
        final_gate["queue_snapshot_hash"] = current_queue_snapshot.get("sha256")
        final_gate["identity"] = identity
        final_gate["order_queued_id"] = identity.get("order_queued_id")
        final_gate["final_send_gate_readiness_result"] = readiness
        final_gate["final_send_gate_input_adapter_result"] = input_adapter
        final_gate["final_send_gate_orchestrator_result"] = orchestrator
        return final_gate

    def _queue_data_for_manual_order_action(self, queue_path: Path) -> tuple[dict[str, object], list[object], list[str]]:
        try:
            data = json.loads(queue_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {}, [], [f"failed to read order_queue json: {exc}"]
        if not isinstance(data, dict):
            return {}, [], ["order_queue root must be an object"]
        orders = data.get("orders")
        if not isinstance(orders, list):
            return data, [], ["order_queue orders must be a list"]
        return data, orders, []


    def _pending_cancel_duplicate_reason(self, orders: list[object], original_order_no: str) -> str:
        active_statuses = {
            "ORDER_QUEUED",
            "DISPATCH_CLAIMED",
            "SEND_ATTEMPTED",
            "SEND_CALL_IN_PROGRESS",
            "SEND_CALL_ACCEPTED",
            "SEND_UNCERTAIN",
            "BROKER_ACCEPTED",
        }
        for item in orders:
            record = item if isinstance(item, dict) else {}
            execution_request = record.get("execution_request")
            request_preview = execution_request.get("request_preview") if isinstance(execution_request, dict) else {}
            if not isinstance(request_preview, dict):
                continue
            if str(request_preview.get("order_action") or "").strip().upper() not in {"CANCEL", "MODIFY"}:
                continue
            if str(request_preview.get("original_order_no") or "").strip() != original_order_no:
                continue
            if record.get("original_order_effect_confirmed") is True:
                continue
            if str(record.get("status") or "").strip().upper() in active_statuses:
                return "active cancel/modify request already exists for original_order_no"
        return ""


    def _build_manual_cancel_order_queued_preview(
        self,
        source_order: dict[str, object],
        *,
        queue_revision: object,
    ) -> dict[str, object]:
        source_order_id = str(source_order.get("order_id") or source_order.get("id") or "").strip()
        source_signal_id = str(source_order.get("source_signal_id") or "").strip()
        broker_order_no = str(source_order.get("broker_order_no") or "").strip()
        account_no = str(source_order.get("account_no") or "").strip()
        code = str(source_order.get("code") or "").strip()
        side = str(source_order.get("side") or "").strip().upper()
        remaining_quantity = int(source_order.get("remaining_quantity") or 0)
        suffix = uuid4().hex[:12]
        order_id = f"{source_order_id}_CANCEL_{suffix}"
        execution_id = f"EXEC_CANCEL_{suffix}"
        lock_id = f"LOCK_CANCEL_{suffix}"
        candidate_id = f"CANCEL_CANDIDATE_{suffix}"
        queue_pending_id = f"QUEUE_PENDING_{candidate_id}"
        hash_payload = {
            "action": "CANCEL",
            "source_order_id": source_order_id,
            "broker_order_no": broker_order_no,
            "account_no": account_no,
            "code": code,
            "side": side,
            "quantity": remaining_quantity,
            "lock_id": lock_id,
        }
        request_hash = hashlib.sha256(
            json.dumps(hash_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        execution_request = {
            "execution_id": execution_id,
            "order_id": order_id,
            "source_signal_id": source_signal_id,
            "lock_id": lock_id,
            "request_hash": request_hash,
            "guard_snapshot": {"account_no": account_no, "source_queue_revision": queue_revision},
            "request_preview": {
                "account_no": account_no,
                "screen_no": project_order_default_screen_no(),
                "side": side,
                "order_action": "CANCEL",
                "code": code,
                "quantity": remaining_quantity,
                "price": 0,
                "hoga": "LIMIT",
                "original_order_no": broker_order_no,
                "source_order_id": source_order_id,
            },
        }
        return {
            "write_preview": True,
            "write_stage": "order_queued_record_preview_created",
            "next_stage": "QUEUE_WRITE_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [],
            "order_queued_record_preview": {
                "id": f"ORDER_QUEUED_{order_id}",
                "status": "ORDER_QUEUED",
                "source": "execution_queue_pending",
                "source_signal_id": source_signal_id,
                "order_id": order_id,
                "candidate_id": candidate_id,
                "queue_pending_id": queue_pending_id,
                "request_hash": request_hash,
                "lock_id": lock_id,
                "execution_id": execution_id,
                "execution_request": execution_request,
                "queue_contract_version": "manual-cancel-1",
                "send_order_called": False,
                "execution_enabled": False,
                "blocked_reasons": [],
                "account_no": account_no,
                "code": code,
                "side": side,
                "quantity": remaining_quantity,
                "price": 0,
                "order_type": "LIMIT",
                "order_action": "CANCEL",
                "cancel_source_order_id": source_order_id,
            },
        }


    def queue_pending_order_cancellations_for_stock_automatically(
        self,
        code: str,
        routine_instance_id: str,
        *,
        trading_day: str,
        started_at: str,
        side_scope: str | None = None,
        account_no: str | None = None,
    ) -> dict[str, object]:
        """Queue and dispatch cancel requests through the existing final gate."""

        clean_code = str(code or "").strip()
        clean_routine_id = str(routine_instance_id or "").strip()
        clean_trading_day = str(trading_day or "").strip()
        clean_started_at = str(started_at or "").strip()
        clean_side_scope = str(side_scope or CANCEL_SIDE_SCOPE_ALL).strip().upper()
        clean_account_no = str(account_no or "").strip()
        result: dict[str, object] = {
            "ok": False,
            "code": clean_code,
            "routine_instance_id": clean_routine_id,
            "trading_day": clean_trading_day,
            "started_at": clean_started_at,
            "side_scope": clean_side_scope,
            "account_no": clean_account_no,
            "target_buy_order_count": 0,
            "cancel_requested": 0,
            "cancel_pending": 0,
            "remaining_buy_pending_quantity": 0,
            "has_remaining_buy_pending": False,
            "sell_untouched": True,
            "cancel_order_identities": [],
            "blocked_reasons": [],
            "results": [],
        }
        blocked = result["blocked_reasons"]
        if not clean_code:
            blocked.append("stock code is required")
        if not clean_routine_id:
            blocked.append("routine instance id is required")
        if clean_side_scope not in {
            CANCEL_SIDE_SCOPE_ALL,
            CANCEL_SIDE_SCOPE_BUY_ONLY,
        }:
            blocked.append("side scope must be ALL or BUY_ONLY")
        if clean_side_scope == CANCEL_SIDE_SCOPE_BUY_ONLY and not clean_account_no:
            blocked.append("BUY_ONLY cancellation requires account_no")
        if blocked:
            return result

        queue_path = self._context.order_queue_path()
        _data, orders, issues = self._queue_data_for_manual_order_action(
            queue_path
        )
        if issues:
            blocked.extend(issues)
            return result

        matching_code_orders = [
            item
            for item in orders
            if isinstance(item, dict)
            and str(item.get("code") or "").strip() == clean_code
            and str(item.get("status") or "").strip().upper()
            in _CANCELABLE_BROKER_OPEN_STATUSES
        ]
        if clean_side_scope == CANCEL_SIDE_SCOPE_BUY_ONLY:
            buy_only_orders: list[dict[str, object]] = []
            for record in matching_code_orders:
                side = _queue_order_side(record)
                if side == "SELL":
                    continue
                if side != "BUY":
                    blocked.append(
                        "matching stock pending order lacks a valid order side"
                    )
                    continue
                record_account, account_reason = _queue_order_account(record)
                if account_reason:
                    blocked.append(account_reason)
                    continue
                if record_account != clean_account_no:
                    continue
                remaining_quantity, quantity_reason = _queue_remaining_quantity(
                    record
                )
                if remaining_quantity is None:
                    blocked.append(quantity_reason)
                    continue
                if remaining_quantity <= 0:
                    continue
                buy_only_orders.append(record)
                result["remaining_buy_pending_quantity"] = int(
                    result["remaining_buy_pending_quantity"]
                ) + remaining_quantity
            if blocked:
                return result
            matching_code_orders = buy_only_orders
            result["target_buy_order_count"] = len(matching_code_orders)
            result["has_remaining_buy_pending"] = bool(matching_code_orders)
        if not matching_code_orders:
            result["ok"] = True
            return result

        try:
            scope_started_at = datetime.fromisoformat(clean_started_at)
        except ValueError:
            blocked.append(
                "matching stock pending order cannot be scoped without trade_started_at"
            )
            return result
        if scope_started_at.date().isoformat() != clean_trading_day:
            blocked.append(
                "matching stock pending order trading day and start time do not match"
            )
            return result

        sources: list[dict[str, object]] = []
        for record in matching_code_orders:
            if str(record.get("routine") or "").strip() != clean_routine_id:
                blocked.append(
                    "matching stock pending order lacks the required routine instance identity"
                )
                continue
            try:
                created_at = datetime.fromisoformat(
                    str(record.get("created_at") or "").strip()
                )
            except ValueError:
                blocked.append(
                    "matching stock pending order lacks a valid created_at identity"
                )
                continue
            if created_at.date().isoformat() != clean_trading_day:
                continue
            if created_at < scope_started_at:
                continue
            order_action = str(record.get("order_action") or "").strip().upper()
            if order_action not in {"NEW", "MODIFY"}:
                blocked.append(
                    "matching stock pending order lacks a valid order action"
                )
                continue
            if str(record.get("side") or "").strip().upper() not in {
                "BUY",
                "SELL",
            }:
                blocked.append(
                    "matching stock pending order lacks a valid order side"
                )
                continue
            sources.append(deepcopy(record))

        if blocked:
            return result
        if not sources:
            result["ok"] = True
            return result

        if clean_side_scope == CANCEL_SIDE_SCOPE_ALL:
            result["sell_untouched"] = not any(
                _queue_order_side(record) == "SELL" for record in sources
            )

        for source_order in sources:
            broker_order_no = str(
                source_order.get("broker_order_no") or ""
            ).strip()
            try:
                remaining_quantity = int(
                    source_order.get("remaining_quantity") or 0
                )
            except (TypeError, ValueError):
                remaining_quantity = 0
            if not broker_order_no or remaining_quantity <= 0:
                blocked.append(
                    "cancelable order requires broker_order_no and positive remaining_quantity"
                )
                continue
            result["cancel_order_identities"].append(
                {
                    "order_queued_id": str(source_order.get("id") or "").strip(),
                    "order_id": str(source_order.get("order_id") or "").strip(),
                    "broker_order_no": broker_order_no,
                }
            )
            if self._pending_cancel_duplicate_reason(orders, broker_order_no):
                result["cancel_pending"] = int(result["cancel_pending"]) + 1
                continue

            snapshot = self.queue_file_snapshot(queue_path)
            preview = self._build_manual_cancel_order_queued_preview(
                source_order,
                queue_revision=snapshot.get("revision"),
            )
            current_snapshot = self.queue_file_snapshot(
                queue_path
            )
            if snapshot.get("sha256") != current_snapshot.get("sha256"):
                blocked.append("queue file changed before cancel commit")
                continue
            commit_result = commit_execution_queue_write(
                preview,
                queue_path,
                context={
                    "manual_queue_write_confirmed": True,
                    "manual_pending_cancel_confirmed": True,
                },
                expected_revision=current_snapshot.get("revision"),
            )
            if (
                commit_result.get("committed") is not True
                or commit_result.get("post_write_verified") is not True
            ):
                blocked.extend(
                    list(
                        commit_result.get("blocked_reasons")
                        or ["cancel queue commit failed"]
                    )
                )
                continue

            cancel_record = preview["order_queued_record_preview"]
            send_result = self.send_order_for_order_queued_automatically(
                str(cancel_record.get("id") or ""),
                queue_path=queue_path,
                source_order=source_order,
            )
            result["results"].append(send_result)
            if (
                send_result.get("queue_result_recorded") is True
                or send_result.get("send_order_called") is True
            ):
                result["cancel_requested"] = int(result["cancel_requested"]) + 1
            else:
                blocked.extend(
                    list(
                        send_result.get("blocked_reasons")
                        or send_result.get("issues")
                        or ["cancel SendOrder pipeline blocked"]
                    )
                )

        result["ok"] = not blocked
        return result

    def auto_trade_runtime_state_for_order(
        self,
        order: dict[str, object],
    ) -> dict[str, object]:
        execution_request = order.get("execution_request")
        execution_request_dict = execution_request if isinstance(execution_request, dict) else {}
        request_preview = execution_request_dict.get("request_preview")
        request_preview_dict = request_preview if isinstance(request_preview, dict) else {}
        code = str(order.get("code") or request_preview_dict.get("code") or "").strip()
        if not code:
            return {"found": False, "state": {}, "config": {}, "stock_dir": "", "issues": ["order code is required"]}

        try:
            target_instance_ids = set(self.current_selected_target_instance_ids())
        except Exception:
            target_instance_ids = set()
        try:
            selected_metadata = self.current_selected_routine_row_metadata()
        except Exception:
            selected_metadata = None
        if isinstance(selected_metadata, dict) and not target_instance_ids:
            return {
                "found": False,
                "state": {},
                "config": {},
                "stock_dir": "",
                "issues": [ROUTINE_INSTANCE_REQUIRED_MESSAGE],
            }
        if target_instance_ids:
            root = Path(__file__).resolve().parent
            for stock in self._context.base_stocks():
                stock_path = str(stock.get("stock_path", "") or "").strip()
                if not stock_path:
                    continue
                stock_dir = root / stock_path
                stock_code, _stock_name = parse_stock_folder_name(stock_dir.name)
                if stock_code != code:
                    continue
                config = read_json_dict(stock_dir / "config.json")
                assigned_instance_id = str(
                    stock.get("assigned_routine_instance_id", "") or ""
                ).strip()
                if not assigned_instance_id:
                    assigned_instance_id = str(
                        config.get("assigned_routine_instance_id", "") or ""
                    ).strip()
                if assigned_instance_id not in target_instance_ids:
                    continue
                state = read_json_dict(stock_dir / "state.json")
                return {
                    "found": True,
                    "state": state if isinstance(state, dict) else {},
                    "config": config if isinstance(config, dict) else {},
                    "stock_dir": str(stock_dir),
                    "issues": [],
                }
            return {
                "found": False,
                "state": {},
                "config": {},
                "stock_dir": "",
                "issues": ["runtime stock state is not found for selected routine instance"],
            }

        try:
            routine_dir = self.current_selected_routine_dir()
        except Exception:
            routine_dir = None
        routine_dirs = [routine_dir] if isinstance(routine_dir, Path) else self._context.routine_dirs()
        for candidate_routine_dir in routine_dirs:
            if not isinstance(candidate_routine_dir, Path):
                continue
            for stock_dir in self._context.stock_dirs_in_routine(candidate_routine_dir):
                stock_code, _stock_name = parse_stock_folder_name(Path(stock_dir).name)
                if stock_code != code:
                    continue
                state = read_json_dict(Path(stock_dir) / "state.json")
                config = read_json_dict(Path(stock_dir) / "config.json")
                return {
                    "found": True,
                    "state": state if isinstance(state, dict) else {},
                    "config": config if isinstance(config, dict) else {},
                    "stock_dir": str(stock_dir),
                    "issues": [],
                }
        return {"found": False, "state": {}, "config": {}, "stock_dir": "", "issues": ["runtime stock state is not found"]}

    def auto_trade_execution_block_reasons(self, order: dict[str, object]) -> list[str]:
        runtime = self.auto_trade_runtime_state_for_order(order)
        if runtime.get("found") is not True:
            return list(runtime.get("issues") or ["runtime stock state is not found"])

        state = runtime.get("state")
        state_dict = state if isinstance(state, dict) else {}
        status = str(state_dict.get("status") or "").strip().upper()
        execution_request = order.get("execution_request")
        execution_request_dict = (
            execution_request if isinstance(execution_request, dict) else {}
        )
        request_preview = execution_request_dict.get("request_preview")
        request_preview_dict = (
            request_preview if isinstance(request_preview, dict) else {}
        )
        side = str(
            request_preview_dict.get("side") or order.get("side") or ""
        ).strip().upper()
        order_action = str(
            request_preview_dict.get("order_action")
            or order.get("order_action")
            or "NEW"
        ).strip().upper()
        close_status = status in {
            "EARLY_CLOSE",
            "EARLY_CLOSING",
            "AUTO_CLOSE",
            "AUTO_CLOSING",
        }
        routine_close_order = (
            close_status
            and order_action != "CANCEL"
            and side in {"BUY", "SELL"}
            and auto_trade_setting_close_routine_mode_active(
                state_dict,
                display_status=status,
            )
        )
        routine_order_allowed, routine_order_reason = (
            auto_trade_setting_close_routine_order_allowed(
                state_dict,
                side,
                display_status=status,
            )
            if routine_close_order
            else (True, "")
        )
        close_status_exception = (
            close_status
            and (side == "SELL" or order_action == "CANCEL" or routine_close_order)
        )
        reasons: list[str] = []
        if status != "RUNNING" and not close_status_exception:
            reasons.append("auto trade status is not RUNNING")
        if routine_close_order and not routine_order_allowed:
            reasons.append(routine_order_reason)
        if state_dict.get("trade_enabled") is not True:
            reasons.append("trade_enabled is not true")
        if state_dict.get("real_trade_enabled") is not True:
            reasons.append("real_trade_enabled is not true")
        if state_dict.get("signal_probe_only") is True:
            reasons.append("signal_probe_only is true")
        if state_dict.get("review_required") is True:
            reasons.append("review_required is true")
        if is_emergency_stopped_state(state_dict):
            reasons.append("emergency stop status is active")
        return reasons

    def order_with_execution_request_defaults(
        self,
        order: dict[str, object],
        *,
        source_order: dict[str, object] | None = None,
    ) -> dict[str, object]:
        enriched = dict(order)
        source = source_order if isinstance(source_order, dict) else {}
        execution_request = enriched.get("execution_request")
        execution_request_dict = deepcopy(execution_request) if isinstance(execution_request, dict) else {}
        request_preview = execution_request_dict.get("request_preview")
        request_preview_dict = deepcopy(request_preview) if isinstance(request_preview, dict) else {}
        source_intent = source.get("order_intent")
        source_intent_dict = source_intent if isinstance(source_intent, dict) else {}
        if not str(request_preview_dict.get("side") or "").strip():
            source_side = source.get("side") or source_intent_dict.get("side")
            if source_side:
                request_preview_dict["side"] = source_side
        if str(request_preview_dict.get("hoga") or "").strip().upper() in {"", "UNDECIDED"}:
            source_hoga = source.get("hoga") or source.get("order_type") or source_intent_dict.get("hoga")
            if source_hoga:
                request_preview_dict["hoga"] = source_hoga
        for field in ("code", "quantity", "price", "account_no"):
            if request_preview_dict.get(field) in (None, "") and source.get(field) not in (None, ""):
                request_preview_dict[field] = source.get(field)
        if request_preview_dict:
            execution_request_dict["request_preview"] = request_preview_dict
            enriched["execution_request"] = execution_request_dict
        fallback_fields = {
            "account_no": "account_no",
            "code": "code",
            "side": "side",
            "quantity": "quantity",
            "price": "price",
            "order_type": "hoga",
            "hoga": "hoga",
        }
        for target_key, request_key in fallback_fields.items():
            if enriched.get(target_key) in (None, "") and request_key in request_preview_dict:
                enriched[target_key] = request_preview_dict.get(request_key)
        return enriched

    def send_order_for_order_queued_automatically(
        self,
        order_id: str,
        *,
        queue_path: Path | None = None,
        send_order_callable_override=None,
        source_order: dict[str, object] | None = None,
    ) -> dict[str, object]:
        queue_path = queue_path or self._context.order_queue_path()
        order_id = str(order_id or "").strip()
        if not order_id:
            return {
                "status": "BLOCKED",
                "stage": "order_id",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["ORDER_QUEUED record id is required"],
            }

        snapshot = self.queue_file_snapshot(queue_path)
        read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if read_result.get("ok") is not True:
            return {
                "status": "BLOCKED",
                "stage": "read_order",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": read_result.get("blocked_reasons", []),
            }

        order = read_result.get("order")
        order_dict = order if isinstance(order, dict) else {}
        if order_dict.get("status") != "ORDER_QUEUED":
            return {
                "status": "BLOCKED",
                "stage": "order_status",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["target record status is not ORDER_QUEUED"],
            }

        order_for_execution = self.order_with_execution_request_defaults(order_dict, source_order=source_order)
        auto_reasons = self.auto_trade_execution_block_reasons(order_for_execution)
        if auto_reasons:
            return {
                "status": "BLOCKED",
                "stage": "auto_trade_runtime_state",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": auto_reasons,
            }

        environment = self.build_manual_send_order_environment(order_for_execution, queue_path)
        if send_order_callable_override is not None:
            environment["send_order_callable"] = send_order_callable_override
        if environment.get("send_order_environment_ready") is not True:
            return {
                "status": "BLOCKED",
                "stage": "send_order_environment",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(environment.get("issues") or []),
            }

        current_snapshot = self.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            return {
                "status": "BLOCKED",
                "stage": "stale_queue_snapshot",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["queue file changed before automatic SendOrder dispatch"],
            }

        latest_read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if latest_read_result.get("ok") is not True:
            return {
                "status": "BLOCKED",
                "stage": "latest_order_read",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": latest_read_result.get("blocked_reasons", []),
            }
        latest_order = latest_read_result.get("order")
        latest_order_dict = latest_order if isinstance(latest_order, dict) else {}
        latest_order_for_execution = self.order_with_execution_request_defaults(
            latest_order_dict,
            source_order=source_order,
        )
        if latest_order_dict.get("status") != "ORDER_QUEUED":
            return {
                "status": "BLOCKED",
                "stage": "latest_order_status",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["latest target record status is not ORDER_QUEUED"],
            }

        latest_environment = self.build_manual_send_order_environment(latest_order_for_execution, queue_path)
        if send_order_callable_override is not None:
            latest_environment["send_order_callable"] = send_order_callable_override
        if latest_environment.get("send_order_environment_ready") is not True:
            return {
                "status": "BLOCKED",
                "stage": "send_order_environment_after_recheck",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(latest_environment.get("issues") or []),
            }

        identity = self.send_order_identity_from_record(latest_order_for_execution)
        final_gate = self.build_manual_final_send_gate_result(
            latest_order_for_execution,
            latest_environment,
            queue_path,
            snapshot,
            current_snapshot,
        )
        if final_gate.get("final_send_gate_ok") is not True:
            return {
                "status": "BLOCKED",
                "stage": "final_send_gate",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(final_gate.get("blocked_reasons") or ["final send gate blocked"]),
                "final_send_gate_result": final_gate,
            }

        call_preview = self.build_manual_send_order_call_preview(
            latest_order_for_execution,
            latest_environment,
            operator_confirmed=True,
        )
        if call_preview.get("status") != "SEND_ORDER_CALL_READY":
            return {
                "status": "BLOCKED",
                "stage": "send_order_call_preview",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(call_preview.get("issues") or ["send order call preview is not ready"]),
                "send_order_call_preview_result": call_preview,
                "final_send_gate_result": final_gate,
            }

        claim_token = f"AUTO_CLAIM_{uuid4().hex}"
        claim = claim_order_for_dispatch(
            queue_path,
            identity,
            final_gate,
            claim_token=claim_token,
            claim_owner="AUTO_TRADE_SEND_ORDER",
            claim_source="auto_trade_timer",
            context={
                "dispatch_claim_owner": "AUTO_TRADE_SEND_ORDER",
                "dispatch_claim_source": "auto_trade_timer",
                "dispatch_claim_ttl_sec": 60,
                "queue_path": str(queue_path),
                "queue_snapshot_hash": current_snapshot.get("sha256"),
            },
            expected_revision=current_snapshot.get("revision"),
        )
        if claim.get("claimed") is not True or claim.get("post_write_verified") is not True:
            return {
                "status": "BLOCKED",
                "stage": "dispatch_claim",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(claim.get("blocked_reasons") or ["dispatch claim failed"]),
                "dispatch_claim_result": claim,
                "final_send_gate_result": final_gate,
            }

        result = execute_claimed_send_order(
            queue_path,
            identity,
            str(claim.get("dispatch_claim_id") or ""),
            claim_token,
            "AUTO_TRADE_SEND_ORDER",
            claim.get("revision_after"),
            latest_environment.get("send_order_callable"),
            call_preview.get("send_order_args"),
            context={
                "send_order_attempt_owner": "AUTO_TRADE_SEND_ORDER",
                "send_order_attempt_source": "auto_trade_timer",
            },
        )
        result["order_id"] = order_id
        result["dispatch_claim_result"] = claim
        result["final_send_gate_result"] = final_gate
        result["send_order_call_preview_result"] = call_preview
        return result

    def process_executable_order_for_auto_trade(
        self,
        order_id: str,
        *,
        send_order_callable_override=None,
    ) -> dict[str, object]:
        queue_path = self._context.order_queue_path()
        order_id = str(order_id or "").strip()
        read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if read_result.get("ok") is not True:
            return {"processed": False, "stage": "read_executable_order", "order_id": order_id, "blocked_reasons": read_result.get("blocked_reasons", [])}
        order = read_result.get("order")
        order_dict = order if isinstance(order, dict) else {}
        def observed_execution(result: dict[str, object], step: str, passed: bool) -> dict[str, object]:
            try:
                from decision_trace_stage_observer import observe_execution_result

                observe_execution_result(
                    order_dict,
                    result,
                    execution_step=step,
                    passed=passed,
                )
            except Exception:
                pass
            return result
        if order_dict.get("status") != "EXECUTABLE":
            return observed_execution({"processed": False, "stage": "executable_status", "order_id": order_id, "blocked_reasons": ["target record status is not EXECUTABLE"]}, "EXECUTION_ENABLE", False)

        auto_reasons = self.auto_trade_execution_block_reasons(order_dict)
        if auto_reasons:
            return observed_execution({"processed": False, "stage": "auto_trade_runtime_state", "order_id": order_id, "blocked_reasons": auto_reasons}, "EXECUTION_ENABLE", False)

        enable_snapshot = self.queue_file_snapshot(queue_path)
        enable_preview = preview_execution_enable(order_dict, {"operator_confirmed_for_execution_enable": True})
        if enable_preview.get("enable_preview") is not True:
            return observed_execution({"processed": False, "stage": "execution_enable_preview", "order_id": order_id, "blocked_reasons": list(enable_preview.get("blocked_reasons") or [])}, "EXECUTION_ENABLE", False)
        enable_result = commit_execution_enable(
            enable_preview,
            queue_path,
            preview_queue_snapshot=enable_snapshot,
            context={"manual_execution_enable_commit_confirmed": True},
        )
        if enable_result.get("enabled") is not True:
            return observed_execution({"processed": False, "stage": "execution_enable_commit", "order_id": order_id, "blocked_reasons": list(enable_result.get("blocked_reasons") or []), "execution_enable_result": enable_result}, "EXECUTION_ENABLE", False)
        observed_execution({"order_id": order_id, "source_signal_id": order_dict.get("source_signal_id", "")}, "EXECUTION_ENABLE", True)

        enabled_read = self.read_order_from_queue_by_id(order_id, queue_path)
        enabled_order = enabled_read.get("order") if isinstance(enabled_read, dict) else {}
        enabled_order_dict = enabled_order if isinstance(enabled_order, dict) else {}
        guard = self.build_real_preflight_guard_from_gui(enabled_order_dict, operator_confirmed=True)
        guard_reasons = self.real_preflight_guard_block_reasons(guard, include_operator=False)
        if guard_reasons:
            return observed_execution({"processed": False, "stage": "real_preflight_guard", "order_id": order_id, "blocked_reasons": guard_reasons, "execution_enable_result": enable_result}, "REAL_READY", False)

        preflight_snapshot = self.queue_file_snapshot(queue_path)
        preflight_preview = preview_real_order_preflight(
            enabled_order_dict,
            guard,
            {"manual_real_preflight_confirmed": True},
        )
        if preflight_preview.get("real_preflight_preview") is not True:
            return observed_execution({"processed": False, "stage": "real_preflight_preview", "order_id": order_id, "blocked_reasons": list(preflight_preview.get("blocked_reasons") or []), "execution_enable_result": enable_result}, "REAL_READY", False)
        preflight_result = commit_real_order_preflight(
            preflight_preview,
            queue_path,
            preview_queue_snapshot=preflight_snapshot,
            context={"manual_real_preflight_commit_confirmed": True},
        )
        if preflight_result.get("real_preflight_committed") is not True:
            return observed_execution({
                "processed": False,
                "stage": "real_preflight_commit",
                "order_id": order_id,
                "blocked_reasons": list(preflight_result.get("blocked_reasons") or []),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
            }, "REAL_READY", False)
        observed_execution({"order_id": order_id, "source_signal_id": order_dict.get("source_signal_id", "")}, "REAL_READY", True)

        real_ready_read = self.read_order_from_queue_by_id(order_id, queue_path)
        real_ready_order = real_ready_read.get("order") if isinstance(real_ready_read, dict) else {}
        real_ready_order_dict = real_ready_order if isinstance(real_ready_order, dict) else {}
        execution_preview = preview_execution_for_real_ready_order(order_id, guard, queue_path)
        if execution_preview.get("ok") is not True:
            return observed_execution({
                "processed": False,
                "stage": "execution_preview",
                "order_id": order_id,
                "blocked_reasons": list(execution_preview.get("blocked_reasons") or execution_preview.get("issues") or []),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
            }, "FINAL_GUARD", False)

        runtime_commit = self.commit_execution_runtime_for_preview(
            real_ready_order_dict,
            guard,
            execution_preview,
            order_executions_path=self._context.order_executions_path(),
            order_locks_path=self._context.order_locks_path(),
            require_runtime_file_init_dialog=False,
        )
        if runtime_commit.get("runtime_commit_ready") is not True:
            return observed_execution({
                "processed": False,
                "stage": "runtime_commit",
                "order_id": order_id,
                "blocked_reasons": list(runtime_commit.get("blocked_reasons") or []),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
                "execution_preview_result": execution_preview,
                "runtime_commit_result": runtime_commit,
            }, "FINAL_GUARD", False)

        preview_result = execution_preview.get("preview_result")
        preview_result_dict = preview_result if isinstance(preview_result, dict) else {}
        queue_write_preview = execution_preview.get("queue_write_preview_result")
        if not isinstance(queue_write_preview, dict):
            queue_write_preview = preview_result_dict.get("queue_write_preview_result")
        if not isinstance(queue_write_preview, dict) or queue_write_preview.get("write_preview") is not True:
            return observed_execution({
                "processed": False,
                "stage": "queue_write_preview",
                "order_id": order_id,
                "blocked_reasons": ["queue write preview is required"],
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
                "execution_preview_result": execution_preview,
                "runtime_commit_result": runtime_commit,
            }, "FINAL_GUARD", False)

        runtime_commit_result = runtime_commit.get("runtime_commit_result")
        runtime_commit_result_dict = runtime_commit_result if isinstance(runtime_commit_result, dict) else {}
        queue_commit_snapshot = self.queue_file_snapshot(queue_path)
        queue_commit_readiness = evaluate_execution_queue_commit_readiness(
            runtime_commit_result=runtime_commit_result_dict,
            queue_write_preview_result=queue_write_preview,
            queue_path=queue_path,
            confirmations={
                "manual_queue_write_confirmed": True,
                "manual_runtime_queue_write_confirmed": True,
            },
        )
        if queue_commit_readiness.get("status") != "READY_TO_COMMIT_QUEUE":
            return observed_execution({
                "processed": False,
                "stage": "queue_commit_readiness",
                "order_id": order_id,
                "blocked_reasons": list(queue_commit_readiness.get("issues") or ["queue commit readiness policy is not ready"]),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
                "execution_preview_result": execution_preview,
                "runtime_commit_result": runtime_commit,
                "queue_commit_readiness_policy_result": queue_commit_readiness,
            }, "FINAL_GUARD", False)

        queue_commit = commit_execution_queue_manually(
            queue_write_preview,
            queue_path,
            context={
                "manual_queue_write_confirmed": True,
                "manual_runtime_queue_write_confirmed": True,
            },
            queue_commit_readiness_policy_result=queue_commit_readiness,
            manual_queue_commit_after_runtime_confirmed=True,
        )
        if queue_commit.get("manual_commit") is not True:
            return observed_execution({
                "processed": False,
                "stage": "queue_commit",
                "order_id": order_id,
                "blocked_reasons": list(queue_commit.get("blocked_reasons") or ["queue commit failed"]),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
                "execution_preview_result": execution_preview,
                "runtime_commit_result": runtime_commit,
                "queue_commit_readiness_policy_result": queue_commit_readiness,
                "queue_commit_result": queue_commit,
            }, "FINAL_GUARD", False)
        read_back = self.verify_manual_queue_commit_read_back(
            queue_path=queue_path,
            queue_write_preview_result=queue_write_preview,
            runtime_commit_result=runtime_commit_result_dict,
        )
        if read_back.get("verified") is not True:
            return observed_execution({
                "processed": False,
                "stage": "queue_commit_read_back",
                "order_id": order_id,
                "blocked_reasons": list(read_back.get("issues") or ["queue commit read-back failed"]),
                "execution_enable_result": enable_result,
                "real_preflight_result": preflight_result,
                "execution_preview_result": execution_preview,
                "runtime_commit_result": runtime_commit,
                "queue_commit_readiness_policy_result": queue_commit_readiness,
                "queue_commit_result": queue_commit,
                "queue_commit_read_back": read_back,
            }, "FINAL_GUARD", False)

        record = queue_write_preview.get("order_queued_record_preview")
        record_dict = record if isinstance(record, dict) else {}
        order_queued_id = str(record_dict.get("id") or "").strip()
        observed_execution(
            {
                "order_id": order_id,
                "source_signal_id": order_dict.get("source_signal_id", ""),
                "execution_id": record_dict.get("execution_id", ""),
            },
            "FINAL_GUARD",
            True,
        )
        send_order_result = self.send_order_for_order_queued_automatically(
            order_queued_id,
            queue_path=queue_path,
            send_order_callable_override=send_order_callable_override,
            source_order=real_ready_order_dict,
        )
        return {
            "processed": send_order_result.get("queue_result_recorded") is True,
            "stage": "send_order",
            "order_id": order_id,
            "order_queued_id": order_queued_id,
            "blocked_reasons": list(send_order_result.get("blocked_reasons") or send_order_result.get("issues") or []),
            "execution_enable_result": enable_result,
            "real_preflight_result": preflight_result,
            "execution_preview_result": execution_preview,
            "runtime_commit_result": runtime_commit,
            "queue_commit_readiness_policy_result": queue_commit_readiness,
            "queue_commit_result": queue_commit,
            "queue_commit_read_back": read_back,
            "send_order_result": send_order_result,
        }

    def auto_process_executable_orders_for_real_trade(self, *, limit: int = 5) -> dict[str, object]:
        queue_path = self._context.order_queue_path()
        try:
            data = json.loads(queue_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"processed": 0, "blocked": 1, "results": [], "blocked_reasons": [f"failed to read order_queue json: {exc}"]}
        orders = data.get("orders") if isinstance(data, dict) else None
        if not isinstance(orders, list):
            return {"processed": 0, "blocked": 1, "results": [], "blocked_reasons": ["order_queue orders must be a list"]}

        results: list[dict[str, object]] = []
        processed = 0
        blocked = 0
        for item in orders:
            if len(results) >= limit:
                break
            record = item if isinstance(item, dict) else {}
            if record.get("status") != "EXECUTABLE":
                continue
            result = self.process_executable_order_for_auto_trade(str(record.get("id") or ""))
            results.append(result)
            if result.get("processed") is True:
                processed += 1
            else:
                blocked += 1

        return {"processed": processed, "blocked": blocked, "results": results, "blocked_reasons": []}

    # Extracted production methods are inserted below.
