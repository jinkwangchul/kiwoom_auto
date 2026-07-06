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


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
STOCKS_DIR = PROJECT_ROOT / "stocks"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
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


def _write_json(path: Path, data: Any) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def read_order_queue() -> dict[str, Any]:
    data = _read_json(ORDER_QUEUE_PATH, {"version": 1, "updated_at": "", "orders": []})
    if not isinstance(data, dict):
        data = {"version": 1, "updated_at": "", "orders": []}
    if not isinstance(data.get("orders"), list):
        data["orders"] = []
    return data


def write_order_queue(data: dict[str, Any]) -> None:
    data["version"] = data.get("version", 1)
    data["updated_at"] = now_text()
    _write_json(ORDER_QUEUE_PATH, data)


def _read_order_queue_from_path(path: Path) -> dict[str, Any]:
    data = _read_json(path, {"version": 1, "updated_at": "", "orders": []})
    if not isinstance(data, dict):
        data = {"version": 1, "updated_at": "", "orders": []}
    if not isinstance(data.get("orders"), list):
        data["orders"] = []
    return data


def _write_order_queue_to_path(path: Path, data: dict[str, Any]) -> None:
    data["version"] = data.get("version", 1)
    data["updated_at"] = now_text()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_operation_state() -> dict[str, Any]:
    data = _read_json(OPERATION_STATE_PATH, {})
    return data if isinstance(data, dict) else {}


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


def evaluate_operation_policy(order: dict[str, Any]) -> dict[str, Any]:
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

    if side == "BUY" and is_early_close(stock_state):
        return {
            "policy_status": "BLOCKED_POLICY",
            "policy_reason": "조기마감 상태 신규매수 금지",
        }

    if side == "BUY" and is_auto_close(stock_state):
        return {
            "policy_status": "BLOCKED_POLICY",
            "policy_reason": "자동마감 상태 신규매수 금지",
        }

    return {
        "policy_status": "EXECUTABLE",
        "policy_reason": "운영정책 통과",
    }


def apply_operation_policy_gate() -> dict[str, Any]:
    """order_queue.json에 운영정책 차단 결과를 반영한다."""
    data = read_order_queue()
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []
        data["orders"] = orders

    checked = 0
    executable = 0
    blocked_policy = 0
    ignored = 0

    for order in orders:
        if not isinstance(order, dict):
            ignored += 1
            continue

        result = evaluate_operation_policy(order)
        policy_status = result.get("policy_status", "BLOCKED_POLICY")

        if policy_status == "IGNORED":
            ignored += 1
            continue

        checked += 1
        order["policy_status"] = policy_status
        order["policy_reason"] = result.get("policy_reason", "")
        order["policy_checked_at"] = now_text()

        # 실제 주문은 아직 차단 유지
        order["execution_enabled"] = False

        if policy_status == "EXECUTABLE":
            order["status"] = "EXECUTABLE"
            executable += 1
        elif policy_status == "BLOCKED_POLICY":
            order["status"] = "BLOCKED_POLICY"
            blocked_policy += 1
        else:
            ignored += 1

    write_order_queue(data)

    return {
        "checked": checked,
        "executable": executable,
        "blocked_policy": blocked_policy,
        "ignored": ignored,
        "order_queue_path": str(ORDER_QUEUE_PATH),
    }


def apply_operation_policy_gate_for_order(
    order_id: str,
    queue_path: str | Path | None = None,
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

    data = _read_order_queue_from_path(target_path)
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        return {
            "ok": False,
            "status": "skipped",
            "reason": "orders must be a list",
            "order_id": clean_order_id,
            "order_queue_path": str(target_path),
            "changed": False,
        }

    for order in orders:
        if not isinstance(order, dict):
            continue
        if str(order.get("id", "") or "").strip() != clean_order_id:
            continue

        before_status = _norm(order.get("status"))
        if before_status != "APPROVED":
            return {
                "ok": True,
                "status": "skipped",
                "reason": f"target order status is not APPROVED: {before_status}",
                "order_id": clean_order_id,
                "before_status": before_status,
                "after_status": before_status,
                "order_queue_path": str(target_path),
                "changed": False,
            }

        result = evaluate_operation_policy(order)
        policy_status = str(result.get("policy_status", "") or "").upper()
        order["policy_status"] = policy_status
        order["policy_reason"] = result.get("policy_reason", "")
        order["policy_checked_at"] = now_text()
        order["execution_enabled"] = False

        if policy_status == "EXECUTABLE":
            order["status"] = "EXECUTABLE"
        elif policy_status == "BLOCKED_POLICY":
            order["status"] = "BLOCKED_POLICY"
        else:
            return {
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

        _write_order_queue_to_path(target_path, data)
        return {
            "ok": True,
            "status": "updated",
            "reason": order.get("policy_reason", ""),
            "order_id": clean_order_id,
            "before_status": before_status,
            "after_status": order.get("status", ""),
            "policy_status": policy_status,
            "execution_enabled": bool(order.get("execution_enabled", False)),
            "order_queue_path": str(target_path),
            "changed": True,
        }

    return {
        "ok": False,
        "status": "not_found",
        "reason": "order id not found",
        "order_id": clean_order_id,
        "order_queue_path": str(target_path),
        "changed": False,
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
