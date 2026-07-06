# -*- coding: utf-8 -*-
"""operation_policy_gate_reader.py

STEP 11-D: 운영정책 차단 결과 확인 도구 - 표시 보강본.

수정 핵심:
- 승인단계에서 이미 BLOCKED 된 주문은 policy=None 공백 대신
  "승인단계 차단"으로 표시한다.
- 읽기 전용.
- 실제 주문 없음.
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


def display_policy_status(item: dict[str, Any]) -> str:
    policy = item.get("policy_status")
    status = str(item.get("status", "") or "").upper()
    approval = str(item.get("approval_status", "") or "").upper()

    if policy:
        return str(policy)
    if status == "BLOCKED" or approval == "BLOCKED":
        return "승인단계 차단"
    if status == "PENDING":
        return "정책검사 전"
    return "정책검사 대상 아님"


def display_policy_reason(item: dict[str, Any]) -> str:
    policy_reason = item.get("policy_reason")
    approval_reason = item.get("approval_reason")

    if policy_reason:
        return str(policy_reason)
    if approval_reason:
        return f"승인단계: {approval_reason}"
    return "-"


def main() -> None:
    data = read_queue()
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []

    print("=" * 80)
    print("운영정책 차단 결과")
    print("=" * 80)
    print(f"파일: {ORDER_QUEUE_PATH}")
    print(f"전체 주문 후보: {len(orders)}")

    if not orders:
        print("[INFO] 주문 후보 없음")
        return

    status_counter = Counter(str(item.get("status", "")).upper() for item in orders if isinstance(item, dict))
    policy_counter = Counter(display_policy_status(item) for item in orders if isinstance(item, dict))

    print("\n[주문 상태]")
    for status, count in status_counter.most_common():
        print(f"- {status}: {count}")

    print("\n[정책/차단 상태]")
    for status, count in policy_counter.most_common():
        print(f"- {status}: {count}")

    print("\n[상세]")
    for item in orders:
        if not isinstance(item, dict):
            continue
        print(
            f"- {item.get('code')} {item.get('name')} | "
            f"{item.get('side')} | "
            f"status={item.get('status')} | "
            f"approval={item.get('approval_status')} | "
            f"policy={display_policy_status(item)} | "
            f"reason={display_policy_reason(item)} | "
            f"qty={item.get('quantity')} | "
            f"execution_enabled={item.get('execution_enabled')}"
        )


if __name__ == "__main__":
    main()
