# -*- coding: utf-8 -*-
"""kiwoom_order_adapter.py

STEP 14: Kiwoom 주문 어댑터 Stub.

역할:
- 실제 Kiwoom SendOrder 호출부를 격리하기 위한 어댑터 파일.
- 현재 단계에서는 SendOrder를 절대 호출하지 않는다.
- 주문 요청 객체만 생성한다.

중요:
- Kiwoom API 호출 없음.
- 실제 주문 없음.
- QAxWidget 호출 없음.
- SendOrder 호출 없음.

미래 실제 연결 시 이 파일 내부에서만 Kiwoom API를 다룬다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
REAL_TRADE_GUARD_PATH = PROJECT_ROOT / "runtime" / "real_trade_guard.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def build_kiwoom_order_request(order: dict[str, Any], guard: dict[str, Any]) -> dict[str, Any]:
    """order_queue 주문 1건을 Kiwoom 주문 요청 형태로 변환한다.

    현재는 요청 객체만 생성한다.
    실제 SendOrder 호출은 하지 않는다.
    """
    side = str(order.get("side", "") or "").upper()
    code = str(order.get("code", "") or "").strip()
    quantity = order.get("quantity")
    price = order.get("price")
    account_no = str(guard.get("account_no", "") or "").strip()

    if side == "BUY":
        order_kind = "신규매수"
    elif side == "SELL":
        order_kind = "신규매도"
    else:
        order_kind = "UNKNOWN"

    return {
        "adapter": "kiwoom_order_adapter_stub",
        "send_order_enabled": False,
        "rqname": f"{order_kind}_{code}",
        "screen_no": "9000",
        "account_no": account_no,
        "order_kind": order_kind,
        "code": code,
        "quantity": quantity,
        "price": price,
        "hoga": "UNDECIDED",
        "org_order_no": "",
        "source_order_id": order.get("id"),
        "source_signal_id": order.get("source_signal_id"),
        "note": "STEP14 Stub: 실제 SendOrder 호출 없음",
    }


def send_order_stub(request: dict[str, Any]) -> dict[str, Any]:
    """실제 SendOrder 대신 Stub 결과를 반환한다."""
    return {
        "adapter_status": "ADAPTER_READY",
        "adapter_reason": "Kiwoom 주문 요청 객체 생성 완료. SendOrder 미호출.",
        "send_order_called": False,
        "request": request,
    }


def build_kiwoom_order_request_preview_for_order(
    order_id: Any,
    queue_path: str | Path | None = None,
    guard_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a Kiwoom request preview for one REAL_READY order without saving."""
    target_order_id = str(order_id or "").strip()
    path = Path(queue_path) if queue_path is not None else ORDER_QUEUE_PATH
    guard_file = Path(guard_path) if guard_path is not None else REAL_TRADE_GUARD_PATH

    result: dict[str, Any] = {
        "ok": False,
        "status": "error",
        "order_id": target_order_id,
        "queue_path": str(path),
        "guard_path": str(guard_file),
        "not_saved": True,
        "send_order_called": False,
        "send_order_stub_called": False,
        "changed": False,
    }

    if not target_order_id:
        result.update({"status": "invalid_order_id", "reason": "order_id is required"})
        return result

    data = _read_json(path, {})
    if not isinstance(data, dict):
        result.update({"status": "invalid_queue", "reason": "order_queue root must be an object"})
        return result

    orders = data.get("orders", [])
    if not isinstance(orders, list):
        result.update({"status": "invalid_queue", "reason": "order_queue orders must be a list"})
        return result

    target_order = None
    for order in orders:
        if isinstance(order, dict) and str(order.get("id") or "").strip() == target_order_id:
            target_order = order
            break

    if target_order is None:
        result.update({"status": "not_found", "reason": "order_id not found"})
        return result

    before_status = _norm(target_order.get("status"))
    result["before_status"] = before_status
    if before_status != "REAL_READY":
        result.update({
            "ok": True,
            "status": "skipped",
            "reason": f"target order status is not REAL_READY: {before_status}",
        })
        return result

    if not guard_file.exists():
        result.update({
            "status": "skipped",
            "reason": "real trade guard file not found",
        })
        return result

    guard = _read_json(guard_file, {})
    if not isinstance(guard, dict):
        result.update({
            "status": "skipped",
            "reason": "real trade guard root must be an object",
        })
        return result

    request_preview = build_kiwoom_order_request(target_order, guard)
    result.update({
        "ok": True,
        "status": "preview_built",
        "request_preview": request_preview,
        "side": target_order.get("side"),
        "code": target_order.get("code"),
        "quantity": target_order.get("quantity"),
        "price": target_order.get("price"),
        "execution_enabled": bool(target_order.get("execution_enabled", False)),
    })
    return result
