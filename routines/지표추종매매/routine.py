# -*- coding: utf-8 -*-
"""지표추종매매 routine.py

STEP 5-E 설정 충돌 보강본.

범위:
- routine_macd_engine.py 사용.
- BUY / SELL 신호만 반환.
- 주문, 예산, 체결, 청산, 검토관리 이동은 처리하지 않는다.

수정 핵심:
- context["config"]를 루틴 설정으로 자동 사용하지 않는다.
- 루틴 설정은 context["routine_config"] 또는 context["rules"]만 사용한다.
- 없으면 DEFAULT_MACD_ROUTINE_CONFIG를 사용한다.
"""

from __future__ import annotations

from typing import Any
import json
from pathlib import Path

try:
    from routine_cycle_projection import project_indicator_follow_cycle  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .routine_cycle_projection import project_indicator_follow_cycle  # type: ignore
    except Exception:  # pragma: no cover
        project_indicator_follow_cycle = None

try:
    from routine_buy_execution import build_indicator_follow_buy_intent  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .routine_buy_execution import build_indicator_follow_buy_intent  # type: ignore
    except Exception:  # pragma: no cover
        build_indicator_follow_buy_intent = None

try:
    from routine_sell_execution import build_indicator_follow_sell_intent  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .routine_sell_execution import build_indicator_follow_sell_intent  # type: ignore
    except Exception:  # pragma: no cover
        build_indicator_follow_sell_intent = None


try:
    from routine_macd_engine import (  # type: ignore
        DEFAULT_INDICATOR_FOLLOW_CONFIG,
        evaluate_indicator_follow_routine,
        signal_to_dict,
    )
    DEFAULT_MACD_ROUTINE_CONFIG = DEFAULT_INDICATOR_FOLLOW_CONFIG
    evaluate_macd_routine = evaluate_indicator_follow_routine
    _ENGINE_SOURCE = "routine_macd_engine"
    _IMPORT_ERROR = None
except Exception as first_exc:  # pragma: no cover
    try:
        from .routine_macd_engine import (  # type: ignore
            DEFAULT_INDICATOR_FOLLOW_CONFIG,
            evaluate_indicator_follow_routine,
            signal_to_dict,
        )
        DEFAULT_MACD_ROUTINE_CONFIG = DEFAULT_INDICATOR_FOLLOW_CONFIG
        evaluate_macd_routine = evaluate_indicator_follow_routine
        _ENGINE_SOURCE = "routine_macd_engine"
        _IMPORT_ERROR = None
    except Exception as second_exc:  # pragma: no cover
        try:
            from routine_macd_engine import (  # type: ignore
                DEFAULT_MACD_ROUTINE_CONFIG,
                evaluate_macd_routine,
                signal_to_dict,
            )
            DEFAULT_INDICATOR_FOLLOW_CONFIG = DEFAULT_MACD_ROUTINE_CONFIG
            evaluate_indicator_follow_routine = evaluate_macd_routine
            _ENGINE_SOURCE = "routine_macd_engine"
            _IMPORT_ERROR = None
        except Exception as third_exc:  # pragma: no cover
            try:
                from .routine_macd_engine import (  # type: ignore
                    DEFAULT_MACD_ROUTINE_CONFIG,
                    evaluate_macd_routine,
                    signal_to_dict,
                )
                DEFAULT_INDICATOR_FOLLOW_CONFIG = DEFAULT_MACD_ROUTINE_CONFIG
                evaluate_indicator_follow_routine = evaluate_macd_routine
                _ENGINE_SOURCE = "routine_macd_engine"
                _IMPORT_ERROR = None
            except Exception as fourth_exc:  # pragma: no cover
                DEFAULT_INDICATOR_FOLLOW_CONFIG = None
                DEFAULT_MACD_ROUTINE_CONFIG = None
                evaluate_indicator_follow_routine = None
                evaluate_macd_routine = None
                signal_to_dict = None
                _ENGINE_SOURCE = "IMPORT_FAILED"
                _IMPORT_ERROR = (first_exc, second_exc, third_exc, fourth_exc)


ROUTINE_NAME = "지표추종매매"
ROUTINE_API_VERSION = "0.2"
EXECUTION_ENABLED = False
ROUTINE_TYPE = "INDICATOR_FOLLOW"


def _gate_result(
    allowed: bool,
    reason: str,
    routine_identity: dict[str, Any],
    rules_identity: str,
) -> dict[str, Any]:
    return {
        "allowed": bool(allowed),
        "reason": reason,
        "reasons": [] if allowed else [reason],
        "routine_identity": dict(routine_identity),
        "rules_identity": rules_identity,
    }


def evaluate_execution_admission(
    *,
    subject: dict[str, Any],
    rules: dict[str, Any],
    routine_identity: dict[str, Any],
    rules_identity: str,
) -> dict[str, Any]:
    """Apply this routine's candidate-admission rule."""
    del subject
    principle = rules.get("principle") if isinstance(rules, dict) else None
    allowed = (
        isinstance(principle, dict)
        and principle.get("execution_enabled") is True
    )
    return _gate_result(
        allowed,
        "ROUTINE_EXECUTION_ENABLED" if allowed else "ROUTINE_EXECUTION_DISABLED",
        routine_identity,
        rules_identity,
    )


def evaluate_final_real_order_safety(
    *,
    subject: dict[str, Any],
    rules: dict[str, Any],
    routine_identity: dict[str, Any],
    rules_identity: str,
) -> dict[str, Any]:
    """Re-evaluate this routine's real-order rule from current effective rules."""
    del subject
    safety = rules.get("safety") if isinstance(rules, dict) else None
    allowed = (
        isinstance(safety, dict)
        and safety.get("real_order_allowed") is True
    )
    return _gate_result(
        allowed,
        "ROUTINE_REAL_ORDER_ALLOWED" if allowed else "ROUTINE_REAL_ORDER_NOT_ALLOWED",
        routine_identity,
        rules_identity,
    )


def project_cycle_context(
    *,
    code: str,
    routine_instance_id: str,
    order_queue: Any,
    fills: Any,
    positions: Any,
) -> dict[str, Any]:
    if not callable(project_indicator_follow_cycle):
        return {
            "status": "unresolved",
            "active": False,
            "confirmed_buy_round": None,
            "cumulative_filled_buy_amount": None,
            "holding_qty": 0,
            "avg_price": 0.0,
            "last_buy_order_identity": None,
            "partial_sell": False,
            "cycle_ended": False,
            "unresolved_reason": "CYCLE_PROJECTION_IMPORT_FAILED",
        }
    return project_indicator_follow_cycle(
        code=code,
        routine_instance_id=routine_instance_id,
        order_queue=order_queue,
        fills=fills,
        positions=positions,
    )


def get_routine_info() -> dict[str, Any]:
    return {
        "name": ROUTINE_NAME,
        "api_version": ROUTINE_API_VERSION,
        "execution_enabled": EXECUTION_ENABLED,
        "signal_only": True,
        "engine": _ENGINE_SOURCE,
    }


def _extract_candles(context: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("candles", "bars", "ohlcv"):
        value = context.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _load_rules_json() -> dict[str, Any] | None:
    """루틴 폴더의 rules.json을 읽는다.

    원칙:
    - rules.json은 루틴 전략 설정 파일이다.
    - 종목 config.json과 혼용하지 않는다.
    - 읽기 실패 시 기본 설정으로 후퇴한다.
    """
    rules_path = Path(__file__).resolve().parent / "rules.json"
    try:
        if not rules_path.exists():
            return None
        data = json.loads(rules_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_config(context: dict[str, Any]) -> dict[str, Any] | None:
    """루틴 설정만 추출한다.

    허용:
    - routine_config
    - rules

    금지:
    - config
      종목 config.json과 이름이 충돌하므로 루틴 설정으로 사용하지 않는다.
    """
    for key in ("routine_config", "rules"):
        value = context.get(key)
        if isinstance(value, dict):
            return value

    rules = _load_rules_json()
    if isinstance(rules, dict):
        return rules

    return DEFAULT_INDICATOR_FOLLOW_CONFIG if isinstance(DEFAULT_INDICATOR_FOLLOW_CONFIG, dict) else None


def evaluate(context: dict[str, Any] | None = None) -> dict[str, Any]:
    if _IMPORT_ERROR is not None or evaluate_indicator_follow_routine is None or signal_to_dict is None:
        return {
            "signal": None,
            "reason": f"루틴 엔진 import 실패: {_IMPORT_ERROR}",
            "matched_groups": [],
            "details": [],
            "signal_index": -1,
            "delay_bar": 0,
            "routine": ROUTINE_NAME,
            "execution_enabled": EXECUTION_ENABLED,
            "engine": _ENGINE_SOURCE,
        }

    if context is None:
        context = {}

    if not isinstance(context, dict):
        return {
            "signal": None,
            "reason": "context 형식 오류",
            "matched_groups": [],
            "details": [],
            "signal_index": -1,
            "delay_bar": 0,
            "routine": ROUTINE_NAME,
            "execution_enabled": EXECUTION_ENABLED,
            "engine": _ENGINE_SOURCE,
        }

    candles = _extract_candles(context)
    config = _extract_config(context)
    observer = context.get("decision_trace_observer")
    set_effective_rules = getattr(observer, "set_effective_rules", None)
    if callable(set_effective_rules):
        try:
            set_effective_rules(config)
        except Exception:
            pass

    signal = evaluate_indicator_follow_routine(candles, config, context)
    result = signal_to_dict(signal)
    cycle = context.get("cycle")
    signal_side = str(result.get("signal") or "").strip().upper()
    if isinstance(cycle, dict):
        result["cycle"] = dict(cycle)
        if signal_side == "BUY":
            if str(cycle.get("status") or "").strip().lower() == "unresolved":
                result["signal"] = None
                result["reason"] = "매매사이클 체결 상태를 확인할 수 없어 BUY를 차단합니다."
                result["buy_execution_blocked"] = True
                result["buy_execution_blocked_reason"] = cycle.get("unresolved_reason")
            else:
                confirmed_round = cycle.get("confirmed_buy_round")
                cumulative_amount = cycle.get("cumulative_filled_buy_amount")
                result["buy_execution_runtime_state"] = {
                    "confirmed_current_buy_round": confirmed_round,
                    "confirmed_cumulative_buy_budget": cumulative_amount,
                }
                result["next_buy_round"] = (
                    confirmed_round + 1 if isinstance(confirmed_round, int) else None
                )
                result["buy_phase"] = "BASE" if confirmed_round == 0 else "REPEAT"
                if not callable(build_indicator_follow_buy_intent):
                    result["signal"] = None
                    result["buy_execution_blocked"] = True
                    result["buy_execution_blocked_reason"] = "BUY_EXECUTION_BRIDGE_IMPORT_FAILED"
                else:
                    execution = build_indicator_follow_buy_intent(
                        buy_signal_result=result,
                        context=context,
                    )
                    if execution.get("status") == "READY":
                        result["execution_intent"] = execution.get("execution_intent")
                        result["buy_execution_policy_status"] = "READY"
                    else:
                        result["signal"] = None
                        result["buy_execution_blocked"] = True
                        result["buy_execution_blocked_reason"] = execution.get("reason")
                        result["buy_execution_policy_status"] = "BLOCKED"
        elif signal_side == "SELL":
            if not callable(build_indicator_follow_sell_intent):
                result["signal"] = None
                result["sell_execution_blocked"] = True
                result["sell_execution_blocked_reason"] = "SELL_EXECUTION_BRIDGE_IMPORT_FAILED"
                result["sell_execution_policy_status"] = "BLOCKED"
            else:
                execution = build_indicator_follow_sell_intent(
                    sell_signal_result=result,
                    context=context,
                )
                if execution.get("status") == "READY":
                    result["execution_intent"] = execution.get("execution_intent")
                    result["sell_execution_policy_status"] = "READY"
                else:
                    result["signal"] = None
                    result["sell_execution_blocked"] = True
                    result["sell_execution_blocked_reason"] = execution.get("reason")
                    result["sell_execution_policy_status"] = "BLOCKED"
    elif signal_side == "SELL":
        result["signal"] = None
        result["sell_execution_blocked"] = True
        result["sell_execution_blocked_reason"] = "CYCLE_PROJECTION_UNRESOLVED"
        result["sell_execution_policy_status"] = "BLOCKED"
    result["routine"] = ROUTINE_NAME
    result["execution_enabled"] = EXECUTION_ENABLED
    result["engine"] = _ENGINE_SOURCE
    return result
