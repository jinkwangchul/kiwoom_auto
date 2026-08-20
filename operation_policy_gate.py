# -*- coding: utf-8 -*-
"""operation_policy_gate.py

STEP 11-A: 운영정책 차단기 1차본.

역할:
- order_queue.json에서 APPROVED 주문후보를 읽는다.
- 운영정책상 지금 실행 가능한지 최종 판정한다.
- 실행 가능하면 status=EXECUTABLE.
- 차단이면 status=BLOCKED_POLICY.

중요:
- Kiwoom API 호출 없음.
- 실제 주문 없음.
- 예산 차감 없음.
- 보유/체결 변경 없음.
- state.json / config.json / orders.json 수정 없음.
- order_queue.json의 policy_* / status 필드만 갱신한다.

1차 차단정책:
1. 긴급정지
2. 검토관리
3. 조기마감
4. 자동마감
5. 청산중

상태 소스:
- runtime/operation_state.json
- stocks/{code}_{name}/state.json

지원 키는 현재 프로젝트 상태가 아직 유동적이므로 여러 후보명을 허용한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from account_auto_trade_budget_consumption import (
    canonical_buy_candidate_amount,
    project_account_auto_trade_budget_consumption,
    project_system_total_budget_buy_admission,
)
from execution_queue_writer import mutate_order_queue
from gui_auto_trade_policy import (
    auto_trade_setting_close_routine_mode_active,
    auto_trade_setting_close_routine_order_allowed,
)
from gui_operation_environment import read_system_total_budget_for_recalculation
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED
from production_recovery_state_registry import production_recovery_registry
from runtime_atomic_writer import write_json_atomic


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
STOCKS_DIR = PROJECT_ROOT / "stocks"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
POSITIONS_PATH = RUNTIME_DIR / "positions.json"
OPERATION_STATE_PATH = RUNTIME_DIR / "operation_state.json"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _norm(value)
    return text in {"1", "TRUE", "YES", "Y", "ON", "ACTIVE", "ENABLED"}


def _first_truthy(data: dict[str, Any], keys: list[str]) -> bool:
    for key in keys:
        if _truthy(data.get(key)):
            return True
    return False


def _normalize_stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("A"):
        text = text[1:]
    return text if len(text) == 6 and text.isdigit() else ""


def _normalized_stock_codes(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[str] = []
    for value in values:
        code = _normalize_stock_code(value)
        if code and code not in result:
            result.append(code)
    return sorted(result)


PREVIOUS_CLOSE_SESSION_FIELDS = (
    "operation_closing_started_at",
    "operation_close_reason",
    "operation_ended_at",
    "operation_end_reason",
)


def read_order_queue() -> dict[str, Any]:
    data = _read_json(ORDER_QUEUE_PATH, {"version": 1, "updated_at": "", "orders": []})
    if not isinstance(data, dict):
        data = {"version": 1, "updated_at": "", "orders": []}
    if not isinstance(data.get("orders"), list):
        data["orders"] = []
    return data


def read_operation_state() -> dict[str, Any]:
    data = _read_json(OPERATION_STATE_PATH, {})
    return data if isinstance(data, dict) else {}


def write_global_emergency_stop_state(
    *,
    emergency_stop: bool,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Atomically merge the global emergency-stop latch into operation_state."""
    data = read_operation_state()
    when = timestamp or now_text()

    data["emergency_stop"] = emergency_stop
    if emergency_stop:
        data["emergency_stopped_at"] = when
        data["emergency_reason"] = "USER_EMERGENCY_STOP"
        data["emergency_source"] = "CONTROL_WINDOW"
    else:
        data["emergency_released_at"] = when
        data["emergency_reason"] = ""
        data["emergency_source"] = ""

    result = write_json_atomic(OPERATION_STATE_PATH, data)
    ok = result.get("status") == "OK" and result.get("written") is True
    return {
        "ok": ok,
        "emergency_stop": emergency_stop,
        "operation_state_path": str(OPERATION_STATE_PATH),
        **result,
    }


def write_global_operation_running_state(
    *,
    participant_stock_codes: Any = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Atomically merge today's global operation RUNNING state."""
    data = read_operation_state()
    when = timestamp or now_text()
    operation_date = when[:10]

    already_running_today = (
        str(data.get("operation_date") or "").strip() == operation_date
        and str(data.get("operation_status") or "").strip().upper() == "RUNNING"
        and bool(str(data.get("operation_started_at") or "").strip())
    )
    existing_participants = (
        _normalized_stock_codes(data.get("operation_participant_stock_codes"))
        if already_running_today
        else []
    )
    new_participants = _normalized_stock_codes(participant_stock_codes)
    participants = sorted({*existing_participants, *new_participants})

    data["operation_date"] = operation_date
    data["operation_status"] = "RUNNING"
    if not already_running_today:
        data["operation_started_at"] = when
        for key in PREVIOUS_CLOSE_SESSION_FIELDS:
            data.pop(key, None)
    data["operation_updated_at"] = when
    data["operation_participant_stock_codes"] = participants

    result = write_json_atomic(OPERATION_STATE_PATH, data)
    ok = result.get("status") == "OK" and result.get("written") is True
    return {
        "ok": ok,
        "started_new_session": not already_running_today,
        "operation_date": operation_date,
        "operation_status": "RUNNING",
        "operation_participant_stock_codes": participants,
        "operation_state_path": str(OPERATION_STATE_PATH),
        **result,
    }


def write_global_operation_closing_state(
    *,
    close_reason: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Atomically merge today's global operation CLOSING state."""

    data = read_operation_state()
    when = timestamp or now_text()
    operation_date = when[:10]
    current_date = str(data.get("operation_date") or "").strip()
    current_status = str(data.get("operation_status") or "").strip().upper()

    if current_date != operation_date:
        return {
            "ok": False,
            "blocked": True,
            "reason": "operation_date is not today",
            "operation_date": current_date,
            "requested_operation_date": operation_date,
            "operation_status": current_status,
            "operation_state_path": str(OPERATION_STATE_PATH),
            "written": False,
        }
    if current_status not in {"RUNNING", "CLOSING"}:
        return {
            "ok": False,
            "blocked": True,
            "reason": f"operation_status is not RUNNING or CLOSING: {current_status}",
            "operation_date": current_date,
            "operation_status": current_status,
            "operation_state_path": str(OPERATION_STATE_PATH),
            "written": False,
        }

    already_closing_today = current_status == "CLOSING"
    closing_started_at = str(data.get("operation_closing_started_at") or "").strip()
    existing_reason = str(data.get("operation_close_reason") or "").strip()
    clean_reason = str(close_reason or "").strip().upper()

    data["operation_status"] = "CLOSING"
    if not already_closing_today or not closing_started_at:
        data["operation_closing_started_at"] = when
        closing_started_at = when
    else:
        data["operation_closing_started_at"] = closing_started_at
    data["operation_updated_at"] = when
    if not already_closing_today or not existing_reason:
        data["operation_close_reason"] = clean_reason
        existing_reason = clean_reason
    else:
        data["operation_close_reason"] = existing_reason

    result = write_json_atomic(OPERATION_STATE_PATH, data)
    ok = result.get("status") == "OK" and result.get("written") is True
    return {
        "ok": ok,
        "operation_date": operation_date,
        "operation_status": "CLOSING",
        "operation_closing_started_at": closing_started_at,
        "operation_close_reason": existing_reason,
        "operation_state_path": str(OPERATION_STATE_PATH),
        **result,
    }


def write_global_operation_normal_ended_state(
    *,
    timestamp: str | None = None,
    operation_end_reason: str = "ALL_PARTICIPANTS_COMPLETE",
    operation_state_path: str | Path | None = None,
) -> dict[str, Any]:
    """Atomically merge today's global operation NORMAL_ENDED state."""

    target_path = Path(operation_state_path) if operation_state_path is not None else OPERATION_STATE_PATH
    data = _read_json(target_path, {})
    if not isinstance(data, dict):
        data = {}

    when = timestamp or now_text()
    operation_date = when[:10]
    current_date = str(data.get("operation_date") or "").strip()
    current_status = str(data.get("operation_status") or "").strip().upper()
    participants = _normalized_stock_codes(data.get("operation_participant_stock_codes"))

    if current_date != operation_date:
        return {
            "ok": False,
            "blocked": True,
            "reason": "operation_date is not today",
            "operation_date": current_date,
            "requested_operation_date": operation_date,
            "operation_status": current_status,
            "operation_state_path": str(target_path),
            "written": False,
        }
    if current_status not in {"CLOSING", "NORMAL_ENDED"}:
        return {
            "ok": False,
            "blocked": True,
            "reason": f"operation_status is not CLOSING or NORMAL_ENDED: {current_status}",
            "operation_date": current_date,
            "operation_status": current_status,
            "operation_state_path": str(target_path),
            "written": False,
        }
    if not participants:
        return {
            "ok": False,
            "blocked": True,
            "reason": "operation_participant_stock_codes is empty",
            "operation_date": current_date,
            "operation_status": current_status,
            "operation_state_path": str(target_path),
            "written": False,
        }

    already_normal_ended = current_status == "NORMAL_ENDED"
    ended_at = str(data.get("operation_ended_at") or "").strip()
    existing_reason = str(data.get("operation_end_reason") or "").strip()
    clean_reason = str(operation_end_reason or "").strip().upper() or "ALL_PARTICIPANTS_COMPLETE"

    data["operation_status"] = "NORMAL_ENDED"
    if not already_normal_ended or not ended_at:
        data["operation_ended_at"] = when
        ended_at = when
    else:
        data["operation_ended_at"] = ended_at
    if not already_normal_ended or not existing_reason:
        data["operation_end_reason"] = clean_reason
        existing_reason = clean_reason
    else:
        data["operation_end_reason"] = existing_reason
    data["operation_updated_at"] = when

    result = write_json_atomic(target_path, data)
    written = result.get("status") == "OK" and result.get("written") is True
    read_back = _read_json(target_path, {}) if written else {}
    read_back_ok = (
        isinstance(read_back, dict)
        and str(read_back.get("operation_date") or "").strip() == operation_date
        and str(read_back.get("operation_status") or "").strip().upper() == "NORMAL_ENDED"
        and str(read_back.get("operation_ended_at") or "").strip() == ended_at
        and str(read_back.get("operation_end_reason") or "").strip() == existing_reason
        and _normalized_stock_codes(read_back.get("operation_participant_stock_codes")) == participants
    )
    ok = bool(written and read_back_ok)
    return {
        "ok": ok,
        "operation_date": operation_date,
        "operation_status": "NORMAL_ENDED",
        "operation_ended_at": ended_at,
        "operation_end_reason": existing_reason,
        "operation_participant_stock_codes": participants,
        "operation_state_path": str(target_path),
        "read_back_ok": read_back_ok,
        **result,
    }


def find_stock_dir(code: str, name: str = "") -> Path | None:
    if not STOCKS_DIR.exists():
        return None

    for path in STOCKS_DIR.iterdir():
        if path.is_dir() and path.name.startswith(f"{code}_"):
            return path

    if code and name:
        direct = STOCKS_DIR / f"{code}_{name}"
        if direct.exists() and direct.is_dir():
            return direct

    return None


def read_stock_state(code: str, name: str = "") -> dict[str, Any]:
    stock_dir = find_stock_dir(code, name)
    if stock_dir is None:
        return {}
    data = _read_json(stock_dir / "state.json", {})
    return data if isinstance(data, dict) else {}


def is_emergency_stop(operation_state: dict[str, Any]) -> bool:
    return _first_truthy(
        operation_state,
        [
            "emergency_stop",
            "emergency_stopped",
            "global_emergency_stop",
            "is_emergency_stop",
            "stop_all",
            "trading_halted",
        ],
    )


def is_review_managed(stock_state: dict[str, Any]) -> bool:
    if _first_truthy(
        stock_state,
        [
            "review_required",
            "in_review",
            "review_managed",
            "is_review",
            "manual_review",
        ],
    ):
        return True

    status = _norm(stock_state.get("status"))
    location = _norm(stock_state.get("location") or stock_state.get("current_location"))
    return status in {"REVIEW", "REVIEW_REQUIRED", "검토관리"} or location in {"REVIEW", "REVIEW_REQUIRED", "검토관리"}


def is_early_close(stock_state: dict[str, Any]) -> bool:
    if _first_truthy(stock_state, ["early_close", "early_closing", "is_early_close"]):
        return True
    status = _norm(stock_state.get("status"))
    return status in {"EARLY_CLOSE", "EARLY_CLOSING", "조기마감"}


def is_auto_close(stock_state: dict[str, Any]) -> bool:
    if _first_truthy(stock_state, ["auto_close", "auto_closing", "is_auto_close"]):
        return True
    status = _norm(stock_state.get("status"))
    return status in {"AUTO_CLOSE", "AUTO_CLOSING", "자동마감"}


def is_liquidating(stock_state: dict[str, Any]) -> bool:
    if _first_truthy(stock_state, ["liquidating", "is_liquidating", "clearance", "clearing"]):
        return True
    status = _norm(stock_state.get("status"))
    return status in {"LIQUIDATING", "LIQUIDATION", "청산", "청산중"}


def _account_total_budget_admission(
    order: dict[str, Any],
    orders: list[dict[str, Any]],
) -> dict[str, Any]:
    intent = order.get("execution_intent")
    intent_dict = intent if isinstance(intent, dict) else {}
    account_no = str(order.get("account_no") or intent_dict.get("account_no") or "").strip()
    total_budget = read_system_total_budget_for_recalculation()
    recovery = production_recovery_registry.snapshot()
    identity = getattr(recovery, "identity", None)
    recovery_ready = bool(
        recovery is not None
        and getattr(recovery, "account_status", None) == ACCOUNT_COMPLETED
        and identity is not None
        and str(getattr(identity, "account_no", "") or "").strip() == account_no
        and str(getattr(identity, "trading_day", "") or "").strip()
        == datetime.now().date().isoformat()
    )
    stocks = tuple(getattr(recovery, "stocks", ()) or ()) if recovery is not None else ()
    reconciled_codes = {
        str(getattr(stock, "stock_code", "") or "").strip()
        for stock in stocks
        if getattr(stock, "stock_status", None) == STOCK_RESTORED
        and getattr(stock, "review_required", None) is False
    }
    order_code = str(order.get("code") or "").strip().upper().removeprefix("A")
    if len(reconciled_codes) != len(stocks) or order_code not in reconciled_codes:
        recovery_ready = False

    consumption = project_account_auto_trade_budget_consumption(
        account_no=account_no,
        positions_path=POSITIONS_PATH,
        order_queue_path=ORDER_QUEUE_PATH,
        recovery_complete=recovery_ready,
        reconciled_stock_codes=reconciled_codes,
        order_records=orders,
    )
    try:
        candidate_amount: object = canonical_buy_candidate_amount(order)
    except ValueError:
        candidate_amount = None
    admission = project_system_total_budget_buy_admission(
        total_budget=total_budget,
        account_consumed_amount=(
            consumption.get("consumed_amount")
            if consumption.get("available") is True
            else None
        ),
        candidate_buy_amount=candidate_amount,
    )
    admission.update(
        {
            "account_no": account_no or None,
            "holding_cost": consumption.get("holding_cost"),
            "outstanding_buy_reservation": consumption.get("open_buy_reservation"),
            "consumption_reason": consumption.get("reason"),
            "serialized_by": "execution_queue_writer.mutate_order_queue",
        }
    )
    return admission


def evaluate_operation_policy(
    order: dict[str, Any],
    *,
    account_orders: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """주문후보 1건에 운영정책 차단 여부를 판정한다."""
    status = _norm(order.get("status"))
    side = _norm(order.get("side"))
    code = str(order.get("code", "") or "").strip()
    name = str(order.get("name", "") or "").strip()

    if status not in {"APPROVED", "EXECUTABLE", "BLOCKED_POLICY"}:
        return {
            "policy_status": "IGNORED",
            "policy_reason": f"운영정책 검사 대상 아님: {status}",
        }

    operation_state = read_operation_state()
    stock_state = read_stock_state(code, name)

    if is_emergency_stop(operation_state):
        return {
            "policy_status": "BLOCKED_POLICY",
            "policy_reason": "긴급정지 활성",
        }

    if is_review_managed(stock_state):
        return {
            "policy_status": "BLOCKED_POLICY",
            "policy_reason": "검토관리 종목",
        }

    if is_liquidating(stock_state):
        return {
            "policy_status": "BLOCKED_POLICY",
            "policy_reason": "청산중 종목",
        }

    if (
        side in {"BUY", "SELL"}
        and auto_trade_setting_close_routine_mode_active(
            stock_state,
            display_status=str(stock_state.get("status") or ""),
        )
    ):
        order_allowed, order_reason = auto_trade_setting_close_routine_order_allowed(
            stock_state,
            side,
            display_status=str(stock_state.get("status") or ""),
        )
        if not order_allowed:
            return {
                "policy_status": "BLOCKED_POLICY",
                "policy_reason": order_reason,
            }

    if side == "BUY" and account_orders is not None:
        admission = _account_total_budget_admission(order, account_orders)
        if admission.get("available") is not True:
            return {
                "policy_status": "BLOCKED_POLICY",
                "policy_reason": "SYSTEM_TOTAL_BUDGET_EVIDENCE_UNAVAILABLE",
                "policy_evidence": admission,
            }
        if admission.get("admitted") is not True:
            return {
                "policy_status": "BLOCKED_POLICY",
                "policy_reason": "SYSTEM_TOTAL_BUDGET_EXCEEDED",
                "policy_evidence": admission,
            }
        return {
            "policy_status": "EXECUTABLE",
            "policy_reason": "운영정책 및 시스템 전체예산 통과",
            "policy_evidence": admission,
        }

    return {
        "policy_status": "EXECUTABLE",
        "policy_reason": "운영정책 통과",
    }


def apply_operation_policy_gate() -> dict[str, Any]:
    """order_queue.json에 운영정책 차단 결과를 반영한다."""
    return _apply_operation_policy_gate_canonical()


def apply_operation_policy_gate_for_order(
    order_id: str,
    queue_path: str | Path | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Apply operation policy gate to one APPROVED order candidate only."""
    clean_order_id = str(order_id or "").strip()
    target_path = Path(queue_path) if queue_path is not None else ORDER_QUEUE_PATH

    if not clean_order_id:
        return {
            "ok": False,
            "status": "skipped",
            "reason": "order_id is required",
            "order_id": clean_order_id,
            "order_queue_path": str(target_path),
            "changed": False,
        }

    return _apply_operation_policy_gate_for_order_canonical(
        clean_order_id,
        target_path,
        expected_revision=expected_revision,
    )


def _apply_operation_policy_gate_canonical() -> dict[str, Any]:
    def mutator(data: dict[str, Any]) -> dict[str, Any]:
        orders = data.get("orders", [])
        if not isinstance(orders, list):
            return {
                "blocked": {
                    "checked": 0,
                    "executable": 0,
                    "blocked_policy": 0,
                    "ignored": 0,
                    "reason": "orders must be a list",
                    "order_queue_path": str(ORDER_QUEUE_PATH),
                }
            }

        checked = 0
        executable = 0
        blocked_policy = 0
        ignored = 0

        for order in orders:
            if not isinstance(order, dict):
                ignored += 1
                continue

            result = evaluate_operation_policy(order, account_orders=orders)
            policy_status = result.get("policy_status", "BLOCKED_POLICY")
            if policy_status == "IGNORED":
                ignored += 1
                continue

            checked += 1
            order["policy_status"] = policy_status
            order["policy_reason"] = result.get("policy_reason", "")
            if isinstance(result.get("policy_evidence"), dict):
                order["policy_evidence"] = dict(result["policy_evidence"])
            order["policy_checked_at"] = now_text()
            order["execution_enabled"] = False

            if policy_status == "EXECUTABLE":
                order["status"] = "EXECUTABLE"
                executable += 1
            elif policy_status == "BLOCKED_POLICY":
                order["status"] = "BLOCKED_POLICY"
                blocked_policy += 1
            else:
                ignored += 1

        result = {
            "checked": checked,
            "executable": executable,
            "blocked_policy": blocked_policy,
            "ignored": ignored,
            "order_queue_path": str(ORDER_QUEUE_PATH),
        }
        if checked <= 0:
            return {"blocked": result}
        return {"data": data, "result": result}

    mutation = mutate_order_queue(
        ORDER_QUEUE_PATH,
        mutator,
        operation_name="operation_policy_gate",
        success_stage="operation_policy_gate_applied",
        next_stage="EXECUTION_ENABLE_PREVIEW_REQUIRED",
        default_queue={"version": 1, "updated_at": "", "orders": []},
    )
    return {
        "checked": int(mutation.get("checked", 0) or 0),
        "executable": int(mutation.get("executable", 0) or 0),
        "blocked_policy": int(mutation.get("blocked_policy", 0) or 0),
        "ignored": int(mutation.get("ignored", 0) or 0),
        "order_queue_path": str(ORDER_QUEUE_PATH),
        **mutation,
    }


def _apply_operation_policy_gate_for_order_canonical(
    clean_order_id: str,
    target_path: Path,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    def mutator(data: dict[str, Any]) -> dict[str, Any]:
        orders = data.get("orders", [])
        if not isinstance(orders, list):
            return {
                "blocked": {
                    "ok": False,
                    "status": "skipped",
                    "reason": "orders must be a list",
                    "order_id": clean_order_id,
                    "order_queue_path": str(target_path),
                    "changed": False,
                }
            }

        matches = [
            order
            for order in orders
            if isinstance(order, dict)
            and str(order.get("id", "") or "").strip() == clean_order_id
        ]
        if not matches:
            return {
                "blocked": {
                    "ok": False,
                    "status": "not_found",
                    "reason": "order id not found",
                    "order_id": clean_order_id,
                    "order_queue_path": str(target_path),
                    "changed": False,
                }
            }
        if len(matches) > 1:
            return {
                "blocked": {
                    "ok": False,
                    "status": "duplicate_identity",
                    "reason": "duplicate order id found",
                    "order_id": clean_order_id,
                    "order_queue_path": str(target_path),
                    "changed": False,
                }
            }

        order = matches[0]
        before_status = _norm(order.get("status"))
        before_policy_status = _norm(order.get("policy_status"))

        if before_status in {"EXECUTABLE", "BLOCKED_POLICY"} and before_policy_status == before_status:
            return {
                "blocked": {
                    "ok": True,
                    "status": "noop",
                    "reason": f"target order already {before_status}",
                    "order_id": clean_order_id,
                    "before_status": before_status,
                    "after_status": before_status,
                    "policy_status": before_policy_status,
                    "execution_enabled": bool(order.get("execution_enabled", False)),
                    "order_queue_path": str(target_path),
                    "changed": False,
                }
            }

        if before_status != "APPROVED":
            return {
                "blocked": {
                    "ok": False,
                    "status": "blocked",
                    "reason": f"target order status is not APPROVED: {before_status}",
                    "order_id": clean_order_id,
                    "before_status": before_status,
                    "after_status": before_status,
                    "order_queue_path": str(target_path),
                    "changed": False,
                }
            }

        result = evaluate_operation_policy(order, account_orders=orders)
        policy_status = str(result.get("policy_status", "") or "").upper()
        if policy_status not in {"EXECUTABLE", "BLOCKED_POLICY"}:
            return {
                "blocked": {
                    "ok": True,
                    "status": "skipped",
                    "reason": f"operation policy ignored order: {policy_status}",
                    "order_id": clean_order_id,
                    "before_status": before_status,
                    "after_status": before_status,
                    "policy_status": policy_status,
                    "order_queue_path": str(target_path),
                    "changed": False,
                }
            }

        order["policy_status"] = policy_status
        order["policy_reason"] = result.get("policy_reason", "")
        if isinstance(result.get("policy_evidence"), dict):
            order["policy_evidence"] = dict(result["policy_evidence"])
        order["policy_checked_at"] = now_text()
        order["execution_enabled"] = False
        order["status"] = policy_status

        return {
            "data": data,
            "result": {
                "ok": True,
                "status": "updated",
                "reason": order.get("policy_reason", ""),
                "order_id": clean_order_id,
                "before_status": before_status,
                "after_status": order.get("status", ""),
                "policy_status": policy_status,
                "policy_evidence": result.get("policy_evidence"),
                "execution_enabled": bool(order.get("execution_enabled", False)),
                "order_queue_path": str(target_path),
                "changed": True,
            },
        }

    mutation = mutate_order_queue(
        target_path,
        mutator,
        operation_name="operation_policy_gate_for_order",
        success_stage="operation_policy_gate_applied",
        next_stage="EXECUTION_ENABLE_PREVIEW_REQUIRED",
        expected_revision=expected_revision,
    )
    return {
        "ok": bool(mutation.get("ok", False)),
        "status": str(mutation.get("status", "blocked")),
        "reason": str(mutation.get("reason", "")),
        "order_id": clean_order_id,
        "order_queue_path": str(target_path),
        "changed": bool(mutation.get("changed", False)),
        **mutation,
    }


def summarize_operation_policy() -> dict[str, Any]:
    data = read_order_queue()
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []

    summary = {
        "path": str(ORDER_QUEUE_PATH),
        "total": len(orders),
        "executable": 0,
        "blocked_policy": 0,
        "approved": 0,
        "other": 0,
    }

    for order in orders:
        if not isinstance(order, dict):
            continue
        status = _norm(order.get("status"))
        if status == "EXECUTABLE":
            summary["executable"] += 1
        elif status == "BLOCKED_POLICY":
            summary["blocked_policy"] += 1
        elif status == "APPROVED":
            summary["approved"] += 1
        else:
            summary["other"] += 1

    return summary
