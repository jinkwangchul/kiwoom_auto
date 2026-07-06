# -*- coding: utf-8 -*-
"""routine_signal_log_reader.py

STEP 4: 루틴 신호 로그 확인 도구.

역할:
- runtime/routine_signal_probe.log 파일을 읽는다.
- signal / reason / 종목별 호출 결과를 요약한다.
- GUI, state.json, config.json, orders.json을 수정하지 않는다.

실행:
    python routine_signal_log_reader.py
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "runtime" / "routine_signal_probe.log"


LOG_PATTERN = re.compile(
    r"^\[(?P<time>.*?)\]\s+"
    r"tick=(?P<tick>.*?)\s+"
    r"routine=(?P<routine>.*?)\s+"
    r"stock=(?P<stock>.*?)\s+"
    r"signal=(?P<signal>.*?)\s+"
    r"reason=(?P<reason>.*)$"
)


def parse_line(line: str) -> dict[str, str] | None:
    match = LOG_PATTERN.match(line.strip())
    if not match:
        return None
    return {key: (value or "").strip() for key, value in match.groupdict().items()}


def main() -> None:
    if not LOG_PATH.exists():
        print(f"[ERROR] 로그 파일이 없습니다: {LOG_PATH}")
        print("먼저 test_routine_signal_probe_step3.py 또는 프로그램 실행으로 로그를 생성하세요.")
        return

    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    records = [record for line in lines if (record := parse_line(line)) is not None]

    print("=" * 80)
    print("루틴 신호 로그 요약")
    print("=" * 80)
    print(f"로그 파일: {LOG_PATH}")
    print(f"전체 라인: {len(lines)}")
    print(f"파싱 성공: {len(records)}")

    if not records:
        print("[WARN] 파싱 가능한 로그가 없습니다.")
        return

    signal_counter = Counter(record["signal"] for record in records)
    reason_counter = Counter(record["reason"] for record in records)
    routine_counter = Counter(record["routine"] for record in records)

    print("\n[루틴별]")
    for routine, count in routine_counter.most_common():
        print(f"- {routine}: {count}")

    print("\n[신호별]")
    for signal, count in signal_counter.most_common():
        print(f"- {signal}: {count}")

    print("\n[사유별]")
    for reason, count in reason_counter.most_common():
        print(f"- {reason}: {count}")

    latest_by_stock: dict[str, dict[str, str]] = {}
    for record in records:
        latest_by_stock[record["stock"]] = record

    print("\n[종목별 최신 결과]")
    for stock, record in sorted(latest_by_stock.items()):
        print(
            f"- {stock} | "
            f"routine={record['routine']} | "
            f"signal={record['signal']} | "
            f"reason={record['reason']} | "
            f"time={record['time']}"
        )

    grouped_by_signal: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in latest_by_stock.values():
        grouped_by_signal[record["signal"]].append(record)

    print("\n[최신 결과: 신호별 종목]")
    for signal, items in sorted(grouped_by_signal.items()):
        names = ", ".join(sorted(item["stock"] for item in items))
        print(f"- {signal}: {names}")


if __name__ == "__main__":
    main()
