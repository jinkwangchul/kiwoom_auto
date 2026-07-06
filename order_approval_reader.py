# -*- coding: utf-8 -*-
"""order_approval_reader.py

STEP 10: 주문 승인/차단 결과 확인 도구.

읽기 전용.

실행:
    python order_approval_reader.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"


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
    print("주문 승인/차단 결과")
    print("=" * 80)
    print(f"파일: {ORDER_QUEUE_PATH}")
    print(f"전체 주문 후보: {len(orders)}")

    if not orders:
        print("[INFO] 주문 후보 없음")
        return

    status_counter = Counter(str(item.get("status", "")).upper() for item in orders if isinstance(item, dict))
    approval_counter = Counter(str(item.get("approval_status", "")).upper() for item in orders if isinstance(item, dict))

    print("\n[주문 상태]")
    for status, count in status_counter.most_common():
        print(f"- {status}: {count}")

    print("\n[승인 상태]")
    for status, count in approval_counter.most_common():
        print(f"- {status}: {count}")

    print("\n[상세]")
    for item in orders:
        if not isinstance(item, dict):
            continue
        print(
            f"- {item.get('code')} {item.get('name')} | "
            f"{item.get('side')} | "
            f"status={item.get('status')} | "
            f"candidate={item.get('candidate_status')} | "
            f"approval={item.get('approval_status')} | "
            f"reason={item.get('approval_reason')} | "
            f"qty={item.get('quantity')} | "
            f"execution_enabled={item.get('execution_enabled')}"
        )


if __name__ == "__main__":
    main()
