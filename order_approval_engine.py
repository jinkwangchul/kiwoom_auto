# -*- coding: utf-8 -*-
"""order_approval_engine.py

STEP 10: 주문후보 승인/차단 정책 엔진.

역할:
- runtime/order_queue.json의 PENDING 주문후보를 검토한다.
- 실제 주문 실행 전 승인 가능 여부를 판정한다.
- CANDIDATE_READY이고 안전조건을 만족하면 APPROVED.
- 아니면 BLOCKED.

중요:
- Kiwoom API 호출 없음.
- 실제 주문 없음.
- 예산 차감 없음.
- 보유/체결 변경 없음.
- state.json / config.json / orders.json 수정 없음.
- order_queue.json의 status/approval_* 필드만 갱신한다.

1차 승인 조건:
- status == PENDING
- candidate_status == CANDIDATE_READY
- side in BUY/SELL
- quantity > 0
- execution_enabled == False 유지
- BUY: amount 또는 price 중 최소 하나 존재 권장. 단, quantity가 있으면 승인 가능 후보.
- SELL: quantity > 0

승인 후에도 execution_enabled는 False로 유지한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"


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


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    value_float = _safe_float(value)
    if value_float is None:
        return None
    return int(value_float)


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


def evaluate_order_approval(order: dict[str, Any]) -> dict[str, Any]:
    """주문후보 1건 승인/차단 판정."""
    status = _norm(order.get("status"))
    candidate_status = _norm(order.get("candidate_status"))
    side = _norm(order.get("side"))
    quantity = _safe_int(order.get("quantity"))
    amount = _safe_float(order.get("amount"))
    price = _safe_float(order.get("price"))

    if status != "PENDING":
        return {
            "approval_status": "IGNORED",
            "approval_reason": f"검토 대상 상태 아님: {status}",
        }

    if candidate_status != "CANDIDATE_READY":
        return {
            "approval_status": "BLOCKED",
            "approval_reason": f"주문후보 준비 안 됨: {candidate_status}",
        }

    if side not in {"BUY", "SELL"}:
        return {
            "approval_status": "BLOCKED",
            "approval_reason": f"지원하지 않는 주문 방향: {side}",
        }

    if quantity is None or quantity <= 0:
        return {
            "approval_status": "BLOCKED",
            "approval_reason": "주문수량 없음 또는 0 이하",
        }

    if side == "BUY":
        if amount is None and price is None:
            return {
                "approval_status": "BLOCKED",
                "approval_reason": "BUY 금액/가격 기준 없음",
            }

    if side == "SELL":
        if quantity <= 0:
            return {
                "approval_status": "BLOCKED",
                "approval_reason": "SELL 보유수량 없음",
            }

    return {
        "approval_status": "APPROVED",
        "approval_reason": "주문 승인 가능 후보",
    }


def apply_order_approval_to_queue() -> dict[str, Any]:
    """Apply approval only to PENDING order candidates in order_queue.json."""
    data = read_order_queue()
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []
        data["orders"] = orders

    checked = 0
    approved = 0
    blocked = 0
    ignored = 0

    for order in orders:
        if not isinstance(order, dict):
            ignored += 1
            continue
        if _norm(order.get("status")) != "PENDING":
            ignored += 1
            continue

        checked += 1
        result = evaluate_order_approval(order)
        approval_status = result.get("approval_status", "BLOCKED")

        order["approval_status"] = approval_status
        order["approval_reason"] = result.get("approval_reason", "")
        order["approval_checked_at"] = now_text()

        # 실제 주문은 여전히 막는다.
        order["execution_enabled"] = False

        if approval_status == "APPROVED":
            order["status"] = "APPROVED"
            approved += 1
        elif approval_status == "BLOCKED":
            order["status"] = "BLOCKED"
            blocked += 1
        else:
            ignored += 1

    write_order_queue(data)

    return {
        "checked": checked,
        "approved": approved,
        "blocked": blocked,
        "ignored": ignored,
        "order_queue_path": str(ORDER_QUEUE_PATH),
    }


def apply_order_approval() -> dict[str, Any]:
    """Backward-compatible wrapper for applying approval to PENDING orders."""
    return apply_order_approval_to_queue()


def summarize_approval() -> dict[str, Any]:
    data = read_order_queue()
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []

    summary = {
        "path": str(ORDER_QUEUE_PATH),
        "total": len(orders),
        "approved": 0,
        "blocked": 0,
        "pending": 0,
    }

    for order in orders:
        if not isinstance(order, dict):
            continue
        status = _norm(order.get("status"))
        if status == "APPROVED":
            summary["approved"] += 1
        elif status == "BLOCKED":
            summary["blocked"] += 1
        elif status == "PENDING":
            summary["pending"] += 1

    return summary
