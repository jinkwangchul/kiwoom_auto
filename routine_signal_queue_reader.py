# -*- coding: utf-8 -*-
"""routine_signal_queue_reader.py

STEP 6-B: 루틴 신호 큐 확인 도구.

역할:
- runtime/routine_signals.json 내용을 읽어서 요약한다.
- 읽기 전용.

실행:
    python routine_signal_queue_reader.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
QUEUE_PATH = PROJECT_ROOT / "runtime" / "routine_signals.json"


def read_queue() -> dict[str, Any]:
    if not QUEUE_PATH.exists():
        return {"version": 1, "updated_at": "", "signals": []}
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"version": 1, "updated_at": "", "signals": []}
    except Exception:
        return {"version": 1, "updated_at": "", "signals": []}


def main() -> None:
    data = read_queue()
    signals = data.get("signals", [])
    if not isinstance(signals, list):
        signals = []

    print("=" * 80)
    print("루틴 신호 큐 요약")
    print("=" * 80)
    print(f"파일: {QUEUE_PATH}")
    print(f"updated_at: {data.get('updated_at', '')}")
    print(f"전체 신호: {len(signals)}")

    if not signals:
        print("[INFO] 저장된 신호 없음")
        return

    signal_counter = Counter(str(item.get("signal", "")).upper() for item in signals if isinstance(item, dict))
    status_counter = Counter(str(item.get("status", "")).upper() for item in signals if isinstance(item, dict))

    print("\n[신호별]")
    for signal, count in signal_counter.most_common():
        print(f"- {signal}: {count}")

    print("\n[상태별]")
    for status, count in status_counter.most_common():
        print(f"- {status}: {count}")

    print("\n[신호 목록]")
    for item in signals:
        if not isinstance(item, dict):
            continue
        print(
            f"- {item.get('created_at')} | "
            f"{item.get('routine')} | "
            f"{item.get('code')} {item.get('name')} | "
            f"{item.get('signal')} | "
            f"{item.get('status')} | "
            f"{item.get('reason')} | "
            f"id={item.get('id')}"
        )


if __name__ == "__main__":
    main()
