# -*- coding: utf-8 -*-
"""real_order_executor_stub.py

STEP 14: 실제 주문 실행기 Stub.

역할:
- status=REAL_READY 주문을 읽는다.
- kiwoom_order_adapter.py로 주문 요청 객체를 만든다.
- 실제 Kiwoom SendOrder는 호출하지 않는다.
- order_queue.json에는 ADAPTER_READY 상태만 기록한다.
- runtime/real_order_adapter_stub.log에 기록한다.

중요:
- Kiwoom API 호출 없음.
- 실제 주문 없음.
- SendOrder 호출 없음.
- 예산 차감 없음.
- 보유/체결 변경 없음.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from kiwoom_order_adapter import build_kiwoom_order_request, send_order_stub


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
REAL_TRADE_GUARD_PATH = RUNTIME_DIR / "real_trade_guard.json"
ADAPTER_LOG_PATH = RUNTIME_DIR / "real_order_adapter_stub.log"


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_log(line: str) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with ADAPTER_LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(line.rstrip() + "\n")


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


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


def read_real_trade_guard() -> dict[str, Any]:
    data = _read_json(REAL_TRADE_GUARD_PATH, {})
    return data if isinstance(data, dict) else {}


def run_real_order_adapter_stub() -> dict[str, Any]:
    data = read_order_queue()
    guard = read_real_trade_guard()

    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []
        data["orders"] = orders

    checked = 0
    adapter_ready = 0
    skipped = 0

    for order in orders:
        if not isinstance(order, dict):
            skipped += 1
            continue

        if _norm(order.get("status")) != "REAL_READY":
            skipped += 1
            continue

        checked += 1

        request = build_kiwoom_order_request(order, guard)
        result = send_order_stub(request)

        order["status"] = "ADAPTER_READY"
        order["adapter_status"] = result.get("adapter_status")
        order["adapter_reason"] = result.get("adapter_reason")
        order["send_order_called"] = False
        order["kiwoom_order_request"] = request
        order["adapter_checked_at"] = now_text()

        adapter_ready += 1

        _append_log(
            f"[{now_text()}] ADAPTER_READY "
            f"id={order.get('id')} {order.get('side')} {order.get('code')} {order.get('name')} "
            f"qty={order.get('quantity')} price={order.get('price')} send_order_called=False"
        )

    if checked > 0 or not ORDER_QUEUE_PATH.exists():
        write_order_queue(data)

    return {
        "checked": checked,
        "adapter_ready": adapter_ready,
        "skipped": skipped,
        "order_queue_path": str(ORDER_QUEUE_PATH),
        "adapter_log_path": str(ADAPTER_LOG_PATH),
    }


def summarize_adapter_stub() -> dict[str, Any]:
    data = read_order_queue()
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []

    summary = {
        "path": str(ORDER_QUEUE_PATH),
        "total": len(orders),
        "adapter_ready": 0,
        "real_ready": 0,
        "blocked": 0,
        "other": 0,
    }

    for order in orders:
        if not isinstance(order, dict):
            continue
        status = _norm(order.get("status"))
        if status == "ADAPTER_READY":
            summary["adapter_ready"] += 1
        elif status == "REAL_READY":
            summary["real_ready"] += 1
        elif status in {"BLOCKED", "BLOCKED_POLICY", "BLOCKED_REAL"}:
            summary["blocked"] += 1
        else:
            summary["other"] += 1

    return summary
