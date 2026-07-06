# -*- coding: utf-8 -*-
"""order_queue_reader.py

STEP 9-C: 주문 대기열 확인 도구.

읽기 전용.
"""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
STAGE_REAL_READY_READ = "ORDER_QUEUE_REAL_READY_READ"


def _real_ready_read_result(
    *,
    ok: bool = False,
    order: dict[str, Any] | None = None,
    blocked_reasons: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "stage": STAGE_REAL_READY_READ,
        "order": order,
        "blocked_reasons": blocked_reasons or [],
        "error": error,
    }


def read_real_ready_order_by_id(order_id: Any, queue_path: str | Path | None = None) -> dict[str, Any]:
    """Read one REAL_READY order by id without mutating the queue file."""
    target_order_id = str(order_id or "").strip()
    if not target_order_id:
        return _real_ready_read_result(blocked_reasons=["order_id is required"])

    path = Path(queue_path) if queue_path is not None else ORDER_QUEUE_PATH
    if not path.exists():
        return _real_ready_read_result(blocked_reasons=["order_queue file not found"])

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _real_ready_read_result(error=f"failed to read order_queue json: {exc}")

    if not isinstance(data, dict):
        return _real_ready_read_result(error="order_queue root must be an object")

    orders = data.get("orders", [])
    if not isinstance(orders, list):
        return _real_ready_read_result(error="order_queue orders must be a list")

    for item in orders:
        if not isinstance(item, dict):
            continue
        if str(item.get("id", "") or "").strip() != target_order_id:
            continue

        status = str(item.get("status", "") or "").strip().upper()
        if status != "REAL_READY":
            return _real_ready_read_result(
                blocked_reasons=[f"order status is not REAL_READY: {status or 'EMPTY'}"]
            )
        return _real_ready_read_result(ok=True, order=deepcopy(item))

    return _real_ready_read_result(blocked_reasons=["order_id not found"])


def read_queue() -> dict[str, Any]:
    try:
        if not ORDER_QUEUE_PATH.exists():
            return {"version": 1, "updated_at": "", "orders": []}
        data = json.loads(ORDER_QUEUE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"version": 1, "updated_at": "", "orders": []}
    except Exception:
        return {"version": 1, "updated_at": "", "orders": []}


def main() -> None:
    data = read_queue()
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []

    print("=" * 80)
    print("주문 대기열 요약")
    print("=" * 80)
    print(f"파일: {ORDER_QUEUE_PATH}")
    print(f"updated_at: {data.get('updated_at', '')}")
    print(f"전체 주문 후보: {len(orders)}")

    if not orders:
        print("[INFO] 주문 후보 없음")
        return

    status_counter = Counter(str(item.get("status", "")).upper() for item in orders if isinstance(item, dict))
    side_counter = Counter(str(item.get("side", "")).upper() for item in orders if isinstance(item, dict))
    candidate_counter = Counter(str(item.get("candidate_status", "")).upper() for item in orders if isinstance(item, dict))

    print("\n[상태별]")
    for status, count in status_counter.most_common():
        print(f"- {status}: {count}")

    print("\n[매수/매도]")
    for side, count in side_counter.most_common():
        print(f"- {side}: {count}")

    print("\n[후보상태]")
    for status, count in candidate_counter.most_common():
        print(f"- {status}: {count}")

    print("\n[주문 후보 목록]")
    for item in orders:
        if not isinstance(item, dict):
            continue
        print(
            f"- {item.get('created_at')} | "
            f"{item.get('routine')} | "
            f"{item.get('code')} {item.get('name')} | "
            f"{item.get('side')} | "
            f"{item.get('status')} | "
            f"candidate={item.get('candidate_status')} | "
            f"reason={item.get('candidate_reason')} | "
            f"order_type={item.get('order_type')} | "
            f"qty={item.get('quantity')} | estimated={item.get('quantity_estimated')} | "
            f"amount={item.get('amount')} | price={item.get('price')} | "
            f"execution_enabled={item.get('execution_enabled')} | "
            f"id={item.get('id')}"
        )


if __name__ == "__main__":
    main()
