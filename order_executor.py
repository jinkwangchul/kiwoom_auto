# -*- coding: utf-8 -*-
"""order_executor.py

주문 실행 계층 골격.

현재 단계:
- 실제 키움 OpenAPI 주문 전송은 수행하지 않는다.
- order_manager.handle_routine_signal_for_stock_dir()에서 넘겨받은 주문판정 결과를
  실행 계층으로 전달받을 수 있는 자리를 만든다.
- 필드 테스트 전에는 dry-run executor로만 사용한다.

역할 분리:
- 루틴: BUY/SELL 신호만 생성.
- order_manager: 메인프로그램 기준 주문 가능 여부 판정.
- order_executor: 실제 주문 전송 담당 자리. 현재는 모의 실행만 제공.

주의:
- 이 파일은 키움 주문을 보내지 않는다.
- 실제 SendOrder/dynamicCall 연결은 키움 실연동 단계에서 별도 구현한다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DryRunOrderExecutor:
    """실제 주문 없이 주문 실행 결과 형태만 반환하는 모의 실행기.

    기본값 accept_orders=True:
    - 주문판정이 통과된 신호를 '접수 성공'으로 가정한다.
    - order_manager는 이 결과를 보고 조기/자동마감 루틴 방식의 첫 SELL 메타를 저장할 수 있다.

    테스트에서 접수 실패를 보고 싶으면 accept_orders=False로 생성한다.
    """

    def __init__(self, accept_orders: bool = True) -> None:
        self.accept_orders = bool(accept_orders)

    def __call__(
        self,
        stock_dir: str | Path,
        signal_type: Any,
        decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        signal = str(signal_type or "").strip().upper()
        decision_dict = decision if isinstance(decision, dict) else {}
        accepted = bool(self.accept_orders and decision_dict.get("allowed", True))
        return {
            "accepted": accepted,
            "dry_run": True,
            "signal_type": signal,
            "stock_dir": str(Path(stock_dir)),
            "executed_at": now_text(),
            "message": "DRY_RUN 주문 접수 가정" if accepted else "DRY_RUN 주문 미접수 가정",
        }


def dry_run_order_executor(
    stock_dir: str | Path,
    signal_type: Any,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """함수형 dry-run 주문 실행기.

    order_manager.handle_routine_signal_for_stock_dir(..., order_executor=dry_run_order_executor)
    형태로 연결할 수 있다.
    """
    return DryRunOrderExecutor(accept_orders=True)(stock_dir, signal_type, decision)


class KiwoomOrderExecutor:
    """향후 키움 OpenAPI SendOrder 연결용 자리.

    현재는 의도적으로 NotImplementedError를 발생시킨다.
    실제 주문 전송은 키움 계좌/화면번호/주문유형/호가구분/수량/가격 정책이 확정된 뒤 연결한다.
    """

    def __init__(self, kiwoom: Any) -> None:
        self.kiwoom = kiwoom

    def __call__(
        self,
        stock_dir: str | Path,
        signal_type: Any,
        decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("키움 실제 주문 실행은 아직 연결하지 않았습니다.")
