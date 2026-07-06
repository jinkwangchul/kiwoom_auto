# -*- coding: utf-8 -*-
"""order_candidate_engine.py

STEP 19-B: SELL 후보 생성 시 mock_position fallback 보강본.

핵심 수정:
- 기존에는 state.holding_qty=0을 읽으면 그대로 보유수량 0으로 확정했다.
- 이제는 실제 보유수량 후보가 0 이하일 때 mock_position.mock_holding_qty를 다시 확인한다.
- mock_position.mock_holding_qty > 0이면 SELL 후보를 CANDIDATE_READY로 만든다.

중요:
- 실제 주문 없음.
- Kiwoom API 호출 없음.
- mock_position은 테스트/가상 보유잔고 전용이다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
STOCKS_DIR = PROJECT_ROOT / "stocks"


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    value_float = _safe_float(value)
    if value_float is None:
        return None
    return int(value_float)


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def find_stock_dir(code: str, name: str = "") -> Path | None:
    if not STOCKS_DIR.exists():
        return None

    for path in STOCKS_DIR.iterdir():
        if path.is_dir() and path.name.startswith(f"{code}_"):
            return path

    if code and name:
        direct = STOCKS_DIR / f"{code}_{name}"
        if direct.exists() and direct.is_dir():
            return direct

    return None


def read_stock_config(code: str, name: str = "") -> dict[str, Any]:
    stock_dir = find_stock_dir(code, name)
    if stock_dir is None:
        return {}
    data = _read_json(stock_dir / "config.json", {})
    return data if isinstance(data, dict) else {}


def read_stock_state(code: str, name: str = "") -> dict[str, Any]:
    stock_dir = find_stock_dir(code, name)
    if stock_dir is None:
        return {}
    data = _read_json(stock_dir / "state.json", {})
    return data if isinstance(data, dict) else {}


def read_latest_price(code: str, name: str = "") -> float | None:
    stock_dir = find_stock_dir(code, name)
    if stock_dir is None:
        return None

    latest = _read_json(stock_dir / "latest_price.json", {})
    if isinstance(latest, dict):
        for key in ("price", "latest_price", "current_price", "close"):
            value = _safe_float(latest.get(key))
            if value is not None and value > 0:
                return value

    candles_data = _read_json(stock_dir / "candles.json", {})
    candles = None
    if isinstance(candles_data, dict):
        candles = candles_data.get("candles")
    elif isinstance(candles_data, list):
        candles = candles_data

    if isinstance(candles, list) and candles:
        last = candles[-1]
        if isinstance(last, dict):
            value = _safe_float(last.get("close"))
            if value is not None and value > 0:
                return value

    return None


def get_entry_quantity(config: dict[str, Any]) -> int | None:
    for key in ("entry_quantity", "entry_qty", "buy_quantity", "buy_qty"):
        qty = _safe_int(config.get(key))
        if qty is not None and qty > 0:
            return qty
    return None


def get_entry_amount(config: dict[str, Any]) -> float | None:
    for key in ("entry_amount", "buy_amount", "budget_amount", "order_amount"):
        amount = _safe_float(config.get(key))
        if amount is not None and amount > 0:
            return amount
    return None


def get_real_holding_qty(state: dict[str, Any]) -> int | None:
    for key in (
        "holding_qty",
        "holding_quantity",
        "available_qty",
        "available_quantity",
        "owned_qty",
        "owned_quantity",
        "quantity",
    ):
        qty = _safe_int(state.get(key))
        if qty is not None:
            return qty
    return None


def get_mock_holding_qty(state: dict[str, Any]) -> int | None:
    pos = state.get("mock_position")
    if not isinstance(pos, dict):
        return None
    return _safe_int(pos.get("mock_holding_qty"))


def get_holding_qty_for_sell(state: dict[str, Any]) -> tuple[int, str]:
    """SELL용 보유수량 결정.

    우선순위:
    1) 실제/일반 보유수량이 0보다 크면 사용
    2) 실제/일반 보유수량이 없거나 0 이하이고 mock_position이 0보다 크면 사용
    3) 둘 다 없으면 0
    """
    real_qty = get_real_holding_qty(state)
    if real_qty is not None and real_qty > 0:
        return real_qty, "REAL_OR_STATE_HOLDING"

    mock_qty = get_mock_holding_qty(state)
    if mock_qty is not None and mock_qty > 0:
        return mock_qty, "MOCK_POSITION"

    if real_qty is not None:
        return real_qty, "REAL_OR_STATE_HOLDING_ZERO"

    if mock_qty is not None:
        return mock_qty, "MOCK_POSITION_ZERO"

    return 0, "NO_HOLDING_SOURCE"


def build_buy_candidate(signal: dict[str, Any], config: dict[str, Any], state: dict[str, Any], price: float | None) -> dict[str, Any]:
    qty = get_entry_quantity(config)
    amount = get_entry_amount(config)

    if qty is not None:
        return {
            "candidate_status": "CANDIDATE_READY",
            "candidate_reason": "BUY 진입주수 기준 후보 생성",
            "order_type": "BUY_SIGNAL_CANDIDATE",
            "quantity": qty,
            "quantity_estimated": qty,
            "amount": amount,
            "price": price,
            "budget_source": "entry_quantity",
            "price_basis": "latest_price" if price else "none",
        }

    if amount is not None:
        if price is None or price <= 0:
            return {
                "candidate_status": "AMOUNT_ONLY",
                "candidate_reason": "BUY 금액은 있으나 기준가격/수량 미확정",
                "order_type": "BUY_AMOUNT_CANDIDATE",
                "quantity": None,
                "quantity_estimated": None,
                "amount": amount,
                "price": price,
                "budget_source": "entry_amount",
                "price_basis": "none",
            }

        estimated_qty = int(amount // price)
        if estimated_qty <= 0:
            return {
                "candidate_status": "NEED_BUDGET",
                "candidate_reason": "BUY 금액이 현재가보다 작아 수량 산정 불가",
                "order_type": "UNDECIDED",
                "quantity": None,
                "quantity_estimated": 0,
                "amount": amount,
                "price": price,
                "budget_source": "entry_amount",
                "price_basis": "latest_price",
            }

        return {
            "candidate_status": "CANDIDATE_READY",
            "candidate_reason": "BUY 주문후보 수량 산정 완료",
            "order_type": "BUY_SIGNAL_CANDIDATE",
            "quantity": estimated_qty,
            "quantity_estimated": estimated_qty,
            "amount": amount,
            "price": price,
            "budget_source": "entry_amount",
            "price_basis": "latest_price",
        }

    return {
        "candidate_status": "NEED_BUDGET",
        "candidate_reason": "BUY 진입예산/수량 미설정",
        "order_type": "UNDECIDED",
        "quantity": None,
        "quantity_estimated": None,
        "amount": None,
        "price": price,
        "budget_source": None,
        "price_basis": "latest_price" if price else "none",
    }


def build_sell_candidate(signal: dict[str, Any], config: dict[str, Any], state: dict[str, Any], price: float | None) -> dict[str, Any]:
    holding_qty, source = get_holding_qty_for_sell(state)

    if holding_qty <= 0:
        return {
            "candidate_status": "NO_HOLDING_QTY",
            "candidate_reason": f"SELL 신호이나 보유수량 0 ({source})",
            "order_type": "SELL_NO_HOLDING_CANDIDATE",
            "quantity": 0,
            "quantity_estimated": 0,
            "amount": None,
            "price": price,
            "holding_source": source,
            "price_basis": "latest_price" if price else "none",
        }

    return {
        "candidate_status": "CANDIDATE_READY",
        "candidate_reason": f"SELL 보유수량 기준 후보 생성 ({source})",
        "order_type": "SELL_SIGNAL_CANDIDATE",
        "quantity": holding_qty,
        "quantity_estimated": holding_qty,
        "amount": None,
        "price": price,
        "holding_source": source,
        "price_basis": "latest_price" if price else "none",
    }


def build_order_intent_for_candidate(side: str, candidate: dict[str, Any]) -> dict[str, Any]:
    clean_side = _norm(side)
    price_basis = candidate.get("price_basis")
    intent: dict[str, Any] = {
        "side": clean_side,
        "source": "order_candidate_engine",
        "price_basis": price_basis,
        "source_ui_path": None,
        "unresolved": True,
    }

    if clean_side == "BUY":
        intent["budget_source"] = candidate.get("budget_source")
        intent["unresolved_reason"] = (
            "indicator_follow_ui_state is not read during order candidate generation; "
            "buy UI order method and hoga remain unresolved"
        )
    elif clean_side == "SELL":
        intent["holding_source"] = candidate.get("holding_source")
        intent["unresolved_reason"] = (
            "routine signal payload does not identify sell setting A/B/C source; "
            "sell UI order method and hoga remain unresolved"
        )
    else:
        intent["unresolved_reason"] = "unsupported signal side for order intent"

    return intent


_build_buy_candidate_core = build_buy_candidate
_build_sell_candidate_core = build_sell_candidate


def build_buy_candidate(signal: dict[str, Any], config: dict[str, Any], state: dict[str, Any], price: float | None) -> dict[str, Any]:
    result = _build_buy_candidate_core(signal, config, state, price)
    result["order_intent"] = build_order_intent_for_candidate("BUY", result)
    result["execution_enabled"] = False
    return result


def build_sell_candidate(signal: dict[str, Any], config: dict[str, Any], state: dict[str, Any], price: float | None) -> dict[str, Any]:
    result = _build_sell_candidate_core(signal, config, state, price)
    result["order_intent"] = build_order_intent_for_candidate("SELL", result)
    result["execution_enabled"] = False
    return result


def build_order_candidate(signal: dict[str, Any]) -> dict[str, Any]:
    side = _norm(signal.get("signal"))
    code = str(signal.get("code", "") or "").strip()
    name = str(signal.get("name", "") or "").strip()

    config = read_stock_config(code, name)
    state = read_stock_state(code, name)
    price = read_latest_price(code, name)

    if side == "BUY":
        result = build_buy_candidate(signal, config, state, price)
    elif side == "SELL":
        result = build_sell_candidate(signal, config, state, price)
    else:
        result = {
            "candidate_status": "IGNORED",
            "candidate_reason": f"지원하지 않는 신호: {side}",
            "order_type": "UNDECIDED",
            "quantity": None,
            "quantity_estimated": None,
            "amount": None,
            "price": price,
        }

    if side in {"BUY", "SELL"}:
        result["order_intent"] = build_order_intent_for_candidate(side, result)
    result["execution_enabled"] = False
    return result
