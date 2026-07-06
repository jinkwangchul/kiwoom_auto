# -*- coding: utf-8 -*-
"""real_order_adapter_stub_reader.py

STEP 14: Kiwoom Adapter Stub 결과 확인 도구.

읽기 전용.

실행:
    python real_order_adapter_stub_reader.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
ADAPTER_LOG_PATH = PROJECT_ROOT / "runtime" / "real_order_adapter_stub.log"


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def main() -> None:
    data = read_json(ORDER_QUEUE_PATH, {"orders": []})
    orders = data.get("orders", []) if isinstance(data, dict) else []
    if not isinstance(orders, list):
        orders = []

    print("=" * 80)
    print("Kiwoom Adapter Stub 결과")
    print("=" * 80)
    print(f"order_queue: {ORDER_QUEUE_PATH}")
    print(f"adapter_log: {ADAPTER_LOG_PATH}")
    print(f"전체 주문 후보: {len(orders)}")

    if not orders:
        print("[INFO] 주문 후보 없음")
        return

    status_counter = Counter(str(item.get("status", "")).upper() for item in orders if isinstance(item, dict))

    print("\n[상태별]")
    for status, count in status_counter.most_common():
        print(f"- {status}: {count}")

    print("\n[상세]")
    for item in orders:
        if not isinstance(item, dict):
            continue

        request = item.get("kiwoom_order_request")
        if not isinstance(request, dict):
            request = {}

        print(
            f"- {item.get('code')} {item.get('name')} | "
            f"{item.get('side')} | "
            f"status={item.get('status')} | "
            f"adapter={item.get('adapter_status')} | "
            f"reason={item.get('adapter_reason')} | "
            f"qty={item.get('quantity')} | "
            f"price={item.get('price')} | "
            f"send_order_called={item.get('send_order_called')} | "
            f"rqname={request.get('rqname')} | "
            f"screen_no={request.get('screen_no')} | "
            f"account_no={request.get('account_no')}"
        )


if __name__ == "__main__":
    main()
