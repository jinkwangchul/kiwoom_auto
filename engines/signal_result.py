# -*- coding: utf-8 -*-
"""공통 루틴 신호 결과 포맷.

원칙:
- 루틴의 공식 주문신호는 BUY / SELL만 사용한다.
- 조건 미충족, 데이터 부족, 루틴 비활성 등은 signal=None으로 처리한다.
- 비신호를 별도 주문신호로 승격하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RoutineSignal:
    signal: str | None  # BUY / SELL 또는 None(비신호)
    reason: str
    matched_groups: list[str]
    details: list[str]
    signal_index: int
    delay_bar: int


def signal_to_dict(signal: RoutineSignal) -> dict[str, Any]:
    return {
        "signal": signal.signal,
        "reason": signal.reason,
        "matched_groups": list(signal.matched_groups),
        "details": list(signal.details),
        "signal_index": signal.signal_index,
        "delay_bar": signal.delay_bar,
    }
