# -*- coding: utf-8 -*-
"""order_manager.py

메인프로그램 주문판정 계층 골격 + state.json 연결 훅.

역할:
- 루틴이 만든 BUY/SELL 신호를 메인프로그램 기준으로 1차 판정한다.
- 실제 키움 주문 전송은 여기서 하지 않는다.
- 조기/자동마감 루틴 방식에서는 첫 SELL 주문이 실제 접수된 뒤
  해당 SELL을 당일 마지막 매도신호로 기록하고 이후 BUY/SELL 주문을 차단할 수 있다.
- stock_dir/state.json 연결용 보조 함수만 제공한다.

주의:
- 루틴은 신호만 만든다.
- 메인프로그램이 주문 가능 여부를 최종 판단한다.
- 실제 주문 전송, 주문 실패/거부, 체결/미체결 처리는 추후 키움 실연동 단계에서 붙인다.
- 주문이 실제로 접수/승인되기 전에는 mark_order_accepted 계열 함수를 호출하면 안 된다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gui_auto_trade_policy import (
    auto_trade_setting_close_routine_mode_active,
    auto_trade_setting_close_routine_order_allowed,
    auto_trade_setting_mark_close_routine_final_sell_ordered,
)
from gui_auto_trade_runtime import write_state_json
from runtime_io import read_json_dict


BUY_SIGNALS = {"BUY", "매수"}
SELL_SIGNALS = {"SELL", "매도"}


def normalize_routine_signal(signal_type: Any) -> str:
    """루틴 신호명을 BUY/SELL 내부값으로 정규화한다."""
    raw_text = str(signal_type or "").strip()
    upper_text = raw_text.upper()
    if upper_text in BUY_SIGNALS or raw_text == "매수":
        return "BUY"
    if upper_text in SELL_SIGNALS or raw_text == "매도":
        return "SELL"
    return upper_text


def _state_flag_enabled(state: dict[str, Any], key: str, default: bool = True) -> bool:
    """state의 boolean 계열 값을 안전하게 읽는다."""
    if key not in state:
        return default
    value = state.get(key)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "n", "off", "정지"}


def decide_routine_order(
    state: dict[str, Any] | None,
    signal_type: Any,
    display_status: str = "",
) -> dict[str, Any]:
    """루틴 신호에 대한 메인프로그램 주문판정 결과를 반환한다.

    반환 dict 주요 키:
    - allowed: 실제 주문판정 계속 진행 가능 여부
    - signal_type: BUY/SELL 정규화 신호
    - reason: 차단/허용 사유
    - mark_close_final_sell_after_order: SELL 주문 접수 후 마감 루틴 잠금 메타를 기록해야 하는지 여부

    정책:
    - 조기/자동마감 루틴 방식은 첫 매도신호 전까지 BUY/SELL 신호를 허용한다.
    - 첫 SELL 주문이 실제 접수된 뒤에는 상태 메타를 기록해 이후 BUY/SELL을 차단한다.
    - 이 함수는 주문을 실행하지 않고 판정만 한다.
    """
    state_dict: dict[str, Any] = state if isinstance(state, dict) else {}
    signal = normalize_routine_signal(signal_type)

    if signal not in {"BUY", "SELL"}:
        return {
            "allowed": False,
            "signal_type": signal,
            "reason": "지원하지 않는 루틴 신호",
            "mark_close_final_sell_after_order": False,
        }

    if not _state_flag_enabled(state_dict, "trade_enabled", default=True):
        return {
            "allowed": False,
            "signal_type": signal,
            "reason": "매매 비활성 상태",
            "mark_close_final_sell_after_order": False,
        }

    close_allowed, close_reason = auto_trade_setting_close_routine_order_allowed(
        state_dict,
        signal,
        display_status=display_status,
    )
    if not close_allowed:
        return {
            "allowed": False,
            "signal_type": signal,
            "reason": close_reason,
            "mark_close_final_sell_after_order": False,
        }

    if signal == "BUY" and not _state_flag_enabled(state_dict, "buy_enabled", default=True):
        return {
            "allowed": False,
            "signal_type": signal,
            "reason": "매수 비활성 상태",
            "mark_close_final_sell_after_order": False,
        }

    if signal == "SELL" and not _state_flag_enabled(state_dict, "sell_enabled", default=True):
        return {
            "allowed": False,
            "signal_type": signal,
            "reason": "매도 비활성 상태",
            "mark_close_final_sell_after_order": False,
        }

    mark_final_sell = bool(
        signal == "SELL"
        and auto_trade_setting_close_routine_mode_active(state_dict, display_status=display_status)
    )

    return {
        "allowed": True,
        "signal_type": signal,
        "reason": close_reason if mark_final_sell else "주문판정 통과",
        "mark_close_final_sell_after_order": mark_final_sell,
    }


def mark_order_accepted(
    state: dict[str, Any],
    decision: dict[str, Any] | None,
    source: Any = "routine",
) -> dict[str, Any]:
    """주문 접수 이후 필요한 상태 메타를 갱신한다.

    조기/자동마감 루틴 방식에서 SELL 주문이 접수된 경우,
    이 SELL을 당일 마지막 매도신호로 보고 이후 추가 BUY/SELL 주문을 차단한다.

    실제 주문 실패/거부 시에는 이 함수를 호출하면 안 된다.
    """
    if not isinstance(state, dict):
        return state
    if not isinstance(decision, dict):
        return state
    if not decision.get("allowed"):
        return state
    if not decision.get("mark_close_final_sell_after_order"):
        return state

    return auto_trade_setting_mark_close_routine_final_sell_ordered(
        state,
        source=source,
        reason="루틴 매도신호 주문접수",
    )


def decide_routine_order_for_stock_dir(
    stock_dir: str | Path,
    signal_type: Any,
    display_status: str = "",
) -> dict[str, Any]:
    """종목 runtime 폴더의 state.json을 읽어 루틴 신호 주문판정을 수행한다.

    실제 주문은 하지 않는다.
    """
    path = Path(stock_dir)
    state = read_json_dict(path / "state.json")
    decision = decide_routine_order(state, signal_type, display_status=display_status)
    decision["stock_dir"] = str(path)
    return decision


def mark_routine_order_accepted_for_stock_dir(
    stock_dir: str | Path,
    decision: dict[str, Any] | None,
    source: Any = "routine",
) -> bool:
    """실제 주문 접수 이후 state.json에 필요한 메타를 저장한다.

    사용 기준:
    - decide_routine_order_for_stock_dir() 결과 allowed=True
    - 실제 주문 전송/접수 성공 확인 후 호출
    - 주문 실패/거부/미전송이면 호출 금지
    """
    path = Path(stock_dir)
    state = read_json_dict(path / "state.json")
    if not state:
        return False

    before_marker = (
        state.get("close_routine_final_sell_ordered"),
        state.get("close_routine_final_sell_ordered_at"),
    )
    next_state = mark_order_accepted(state, decision, source=source)
    after_marker = (
        next_state.get("close_routine_final_sell_ordered"),
        next_state.get("close_routine_final_sell_ordered_at"),
    )

    if before_marker == after_marker:
        return True

    return write_state_json(path, next_state)


def decide_and_mark_routine_order_for_stock_dir(
    stock_dir: str | Path,
    signal_type: Any,
    display_status: str = "",
    order_accepted: bool = False,
    source: Any = "routine",
) -> dict[str, Any]:
    """테스트/연결용 통합 훅.

    - 먼저 주문판정만 수행한다.
    - order_accepted=True일 때만 마지막 SELL 메타 저장까지 수행한다.
    - 실제 서비스에서는 키움 주문 접수 성공 후 mark_routine_order_accepted_for_stock_dir()를
      별도로 호출하는 방식이 더 안전하다.
    """
    decision = decide_routine_order_for_stock_dir(
        stock_dir,
        signal_type,
        display_status=display_status,
    )
    decision["state_saved"] = False

    if order_accepted and decision.get("allowed"):
        decision["state_saved"] = mark_routine_order_accepted_for_stock_dir(
            stock_dir,
            decision,
            source=source,
        )

    return decision


def handle_routine_signal_for_stock_dir(
    stock_dir: str | Path,
    signal_type: Any,
    display_status: str = "",
    source: Any = "routine",
    order_executor: Any = None,
) -> dict[str, Any]:
    """루틴 신호를 메인 주문판정 계층에서 처리하는 연결용 훅.

    역할:
    1. stock_dir/state.json을 읽는다.
    2. decide_routine_order_for_stock_dir()로 BUY/SELL 주문 가능 여부를 판정한다.
    3. order_executor가 없으면 실제 주문 없이 판정 결과만 반환한다.
    4. order_executor가 있으면 실제 주문 실행 계층으로 신호를 넘긴다.
    5. 주문 실행 계층이 접수 성공으로 응답한 경우에만 마지막 SELL 메타를 저장한다.

    order_executor 규약:
    - callable이어야 한다.
    - 인자: stock_dir, signal_type, decision
    - 반환:
        True: 주문 접수 성공
        False/None: 주문 미접수 또는 실패
        dict: {"accepted": bool, ...} 형태 권장

    주의:
    - 이 함수도 키움 주문을 직접 실행하지 않는다.
    - 조기/자동마감 루틴 방식에서 첫 SELL 신호가 실제 주문 접수된 뒤에만
      close_routine_final_sell_ordered 메타를 저장한다.
    """
    decision = decide_routine_order_for_stock_dir(
        stock_dir,
        signal_type,
        display_status=display_status,
    )
    decision["order_executor_called"] = False
    decision["order_accepted"] = False
    decision["state_saved"] = False

    if not decision.get("allowed"):
        return decision

    if order_executor is None:
        decision["reason"] = f"{decision.get('reason', '')} / 주문실행기 미연결".strip()
        return decision

    if not callable(order_executor):
        decision["allowed"] = False
        decision["reason"] = "주문실행기가 호출 가능한 객체가 아님"
        return decision

    decision["order_executor_called"] = True
    result = order_executor(Path(stock_dir), decision.get("signal_type"), decision)
    decision["order_executor_result"] = result

    if isinstance(result, dict):
        accepted = bool(result.get("accepted") or result.get("order_accepted"))
    else:
        accepted = bool(result)

    decision["order_accepted"] = accepted
    if accepted:
        decision["state_saved"] = mark_routine_order_accepted_for_stock_dir(
            stock_dir,
            decision,
            source=source,
        )

    return decision



def handle_routine_signal_dry_run_for_stock_dir(
    stock_dir: str | Path,
    signal_type: Any,
    display_status: str = "",
    source: Any = "routine",
) -> dict[str, Any]:
    """테스트용 dry-run 주문판정 통합 훅.

    실제 키움 주문은 보내지 않는다.
    단, order_executor.dry_run_order_executor가 주문 접수 성공을 가정하므로
    조기/자동마감 루틴 방식에서 첫 SELL 신호가 허용된 경우
    close_routine_final_sell_ordered 메타 저장까지 테스트할 수 있다.
    """
    from order_executor import dry_run_order_executor

    return handle_routine_signal_for_stock_dir(
        stock_dir,
        signal_type,
        display_status=display_status,
        source=source,
        order_executor=dry_run_order_executor,
    )
