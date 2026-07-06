# -*- coding: utf-8 -*-
"""real_order_preflight_reader.py

STEP 13-A: 실제 주문 사전검사 결과 확인 도구.

읽기 전용.

실행:
    python real_order_preflight_reader.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
REAL_TRADE_GUARD_PATH = PROJECT_ROOT / "runtime" / "real_trade_guard.json"


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

    guard = read_json(REAL_TRADE_GUARD_PATH, {})

    print("=" * 80)
    print("실제 주문 사전검사 결과")
    print("=" * 80)
    print(f"order_queue: {ORDER_QUEUE_PATH}")
    print(f"real_trade_guard: {REAL_TRADE_GUARD_PATH}")

    if isinstance(guard, dict) and guard:
        print("\n[real_trade_guard]")
        print(f"- real_trade_enabled: {guard.get('real_trade_enabled')}")
        print(f"- kiwoom_logged_in: {guard.get('kiwoom_logged_in')}")
        print(f"- account_selected: {guard.get('account_selected')}")
        print(f"- operator_confirmed: {guard.get('operator_confirmed')}")
        print(f"- account_no: {guard.get('account_no')}")
    else:
        print("\n[real_trade_guard] 없음")

    print(f"\n전체 주문 후보: {len(orders)}")

    if not orders:
        print("[INFO] 주문 후보 없음")
        return

    status_counter = Counter(str(item.get("status", "")).upper() for item in orders if isinstance(item, dict))
    preflight_counter = Counter(str(item.get("real_preflight_status", "")).upper() for item in orders if isinstance(item, dict))

    print("\n[주문 상태]")
    for status, count in status_counter.most_common():
        print(f"- {status}: {count}")

    print("\n[사전검사 상태]")
    for status, count in preflight_counter.most_common():
        print(f"- {status}: {count}")

    print("\n[상세]")
    for item in orders:
        if not isinstance(item, dict):
            continue
        print(
            f"- {item.get('code')} {item.get('name')} | "
            f"{item.get('side')} | "
            f"status={item.get('status')} | "
            f"preflight={item.get('real_preflight_status')} | "
            f"reason={item.get('real_preflight_reason')} | "
            f"qty={item.get('quantity')} | "
            f"order_type={item.get('order_type')} | "
            f"execution_enabled={item.get('execution_enabled')}"
        )


if __name__ == "__main__":
    main()
