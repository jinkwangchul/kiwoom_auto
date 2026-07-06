# -*- coding: utf-8 -*-
"""order_queue.py

STEP 9-C: 주문후보 계산 → 표시 수량 보강본.

수정 핵심:
- order_candidate_engine에서 quantity_estimated가 계산되었는데 quantity가 None이면
  order_queue 저장 시 quantity에도 같은 값을 반영한다.
- order_queue_reader에서 qty가 바로 보이도록 한다.

중요:
- 실제 주문 없음.
- Kiwoom API 호출 없음.
- execution_enabled=False 고정.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
SIGNAL_QUEUE_PATH = RUNTIME_DIR / "routine_signals.json"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"

VALID_SIGNALS = {"BUY", "SELL"}


try:
    from order_candidate_engine import build_order_candidate
except Exception:  # pragma: no cover
    build_order_candidate = None


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_signal_queue() -> dict[str, Any]:
    data = _read_json(SIGNAL_QUEUE_PATH, {"version": 1, "updated_at": "", "signals": []})
    if not isinstance(data, dict):
        data = {"version": 1, "updated_at": "", "signals": []}
    if not isinstance(data.get("signals"), list):
        data["signals"] = []
    return data


def read_order_queue() -> dict[str, Any]:
    data = _read_json(ORDER_QUEUE_PATH, {"version": 1, "updated_at": "", "orders": []})
    if not isinstance(data, dict):
        data = {"version": 1, "updated_at": "", "orders": []}
    if not isinstance(data.get("orders"), list):
        data["orders"] = []
    return data


def write_order_queue(data: dict[str, Any]) -> None:
    data["version"] = data.get("version", 1)
    data["updated_at"] = now_text()
    _write_json(ORDER_QUEUE_PATH, data)


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _order_dedupe_key(order: dict[str, Any]) -> str:
    return "|".join(
        [
            str(order.get("source_signal_id", "")),
            str(order.get("routine", "")),
            str(order.get("code", "")),
            str(order.get("side", "")),
        ]
    )


def _make_order_id(signal: dict[str, Any], index: int) -> str:
    created_at = now_text().replace("-", "").replace(":", "").replace(" ", "_")
    code = str(signal.get("code", "") or "UNKNOWN")
    side = _norm(signal.get("signal"))
    return f"ORDER_{created_at}_{code}_{side}_{index}"


def _normalize_quantity_fields(order: dict[str, Any]) -> None:
    """표시용 quantity 정리.

    quantity_estimated가 있고 quantity가 비어 있으면 quantity에 반영한다.
    실제 주문 가능 여부는 execution_enabled=False로 계속 차단한다.
    """
    if order.get("quantity") is None and order.get("quantity_estimated") is not None:
        order["quantity"] = order.get("quantity_estimated")


def _list_or_empty(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def build_order_provenance_from_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """Build trace-only metadata from the originating routine signal."""
    return {
        "source": "routine_signals",
        "source_signal_id": signal.get("id"),
        "signal_source": signal.get("source"),
        "signal_created_at": signal.get("created_at"),
        "signal_updated_at": signal.get("updated_at"),
        "routine": signal.get("routine"),
        "engine": signal.get("engine") if "engine" in signal else None,
        "code": signal.get("code"),
        "name": signal.get("name"),
        "signal": signal.get("signal"),
        "reason": signal.get("reason"),
        "matched_groups": _list_or_empty(signal.get("matched_groups")),
        "details": _list_or_empty(signal.get("details")),
        "signal_index": signal.get("signal_index"),
        "delay_bar": signal.get("delay_bar"),
        "tick_key": signal.get("tick_key"),
        "source_ui_path": None,
        "rule_path": None,
        "setting_set": None,
        "unresolved": True,
        "unresolved_reason": (
            "signal payload does not include rule path, UI source path, "
            "setting A/B/C, or source candle snapshot"
        ),
    }


def signal_to_order_candidate(signal: dict[str, Any], index: int) -> dict[str, Any] | None:
    side = _norm(signal.get("signal"))
    status = _norm(signal.get("status"))

    if side not in VALID_SIGNALS:
        return None
    if status != "PENDING":
        return None

    code = str(signal.get("code", "") or "").strip()
    name = str(signal.get("name", "") or "").strip()
    routine = str(signal.get("routine", "") or "").strip()
    source_signal_id = str(signal.get("id", "") or "").strip()

    if not code or not routine or not source_signal_id:
        return None

    computed: dict[str, Any] = {}
    if callable(build_order_candidate):
        try:
            computed = build_order_candidate(signal)
        except Exception as exc:
            computed = {
                "candidate_status": "CANDIDATE_ERROR",
                "candidate_reason": f"주문후보 계산 예외: {exc}",
                "execution_enabled": False,
            }

    order = {
        "id": _make_order_id(signal, index),
        "created_at": now_text(),
        "updated_at": now_text(),
        "status": "PENDING",
        "source": "routine_signals",
        "source_signal_id": source_signal_id,
        "routine": routine,
        "code": code,
        "name": name,
        "side": side,
        "order_type": "UNDECIDED",
        "quantity": None,
        "amount": None,
        "price": None,
        "candidate_status": "SAFE_UNDECIDED",
        "candidate_reason": "후보 계산 미수행",
        "budget_source": None,
        "price_basis": "unknown",
        "quantity_estimated": None,
        "execution_enabled": False,
        "reason": str(signal.get("reason", "") or ""),
        "signal_index": signal.get("signal_index"),
        "delay_bar": signal.get("delay_bar"),
        "tick_key": signal.get("tick_key", ""),
        "order_provenance": build_order_provenance_from_signal(signal),
    }

    order.update(computed)
    _normalize_quantity_fields(order)
    order["execution_enabled"] = False
    return order


def build_order_queue_from_signals() -> dict[str, Any]:
    signal_data = read_signal_queue()
    order_data = read_order_queue()

    signals = signal_data.get("signals", [])
    orders = order_data.get("orders", [])

    if not isinstance(signals, list):
        signals = []
    if not isinstance(orders, list):
        orders = []
        order_data["orders"] = orders

    existing_keys = {
        _order_dedupe_key(order)
        for order in orders
        if isinstance(order, dict)
    }

    created = 0
    duplicates = 0
    ignored = 0

    for signal in signals:
        if not isinstance(signal, dict):
            ignored += 1
            continue

        order = signal_to_order_candidate(signal, len(orders) + 1)
        if order is None:
            ignored += 1
            continue

        key = _order_dedupe_key(order)
        if key in existing_keys:
            duplicates += 1
            continue

        orders.append(order)
        existing_keys.add(key)
        created += 1

    if created > 0:
        write_order_queue(order_data)
    else:
        if not ORDER_QUEUE_PATH.exists():
            write_order_queue(order_data)

    return {
        "signals_checked": len(signals),
        "orders_created": created,
        "duplicates": duplicates,
        "ignored": ignored,
        "order_queue_path": str(ORDER_QUEUE_PATH),
    }


def summarize_order_queue() -> dict[str, Any]:
    data = read_order_queue()
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []

    summary = {
        "path": str(ORDER_QUEUE_PATH),
        "total": len(orders),
        "pending": 0,
        "buy": 0,
        "sell": 0,
        "candidate_ready": 0,
        "need_budget": 0,
        "need_holding_qty": 0,
        "no_holding_qty": 0,
    }

    for order in orders:
        if not isinstance(order, dict):
            continue
        if _norm(order.get("status")) == "PENDING":
            summary["pending"] += 1
        side = _norm(order.get("side"))
        if side == "BUY":
            summary["buy"] += 1
        elif side == "SELL":
            summary["sell"] += 1

        candidate_status = _norm(order.get("candidate_status"))
        if candidate_status == "CANDIDATE_READY":
            summary["candidate_ready"] += 1
        elif candidate_status == "NEED_BUDGET":
            summary["need_budget"] += 1
        elif candidate_status == "NEED_HOLDING_QTY":
            summary["need_holding_qty"] += 1
        elif candidate_status == "NO_HOLDING_QTY":
            summary["no_holding_qty"] += 1

    return summary
