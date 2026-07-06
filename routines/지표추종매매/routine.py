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

    signal = evaluate_indicator_follow_routine(candles, config, context)
    result = signal_to_dict(signal)
    result["routine"] = ROUTINE_NAME
    result["execution_enabled"] = EXECUTION_ENABLED
    result["engine"] = _ENGINE_SOURCE
    return result
