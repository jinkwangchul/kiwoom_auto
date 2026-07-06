# -*- coding: utf-8 -*-
"""real_order_preflight.py

STEP 13-A: 실제 주문 사전검사기.

역할:
- runtime/order_queue.json에서 status=EXECUTABLE 주문을 읽는다.
- 실제 주문 전 최종 조건을 검사한다.
- 조건 충족 시 status=REAL_READY.
- 조건 미충족 시 status=BLOCKED_REAL.
- Kiwoom SendOrder 호출은 절대 하지 않는다.

중요:
- Kiwoom API 호출 없음.
- 실제 주문 없음.
- 예산 차감 없음.
- 보유/체결 변경 없음.
- state.json / config.json / orders.json 수정 없음.
- order_queue.json의 real_preflight_* / status 필드만 갱신한다.

기본 차단 원칙:
- real_trade_guard.json이 없으면 차단.
- real_trade_enabled가 True가 아니면 차단.
- kiwoom_logged_in이 True가 아니면 차단.
- account_selected가 True가 아니면 차단.
- operator_confirmed가 True가 아니면 차단.
- order.execution_enabled가 True가 아니면 차단.
- quantity <= 0이면 차단.
- side가 BUY/SELL이 아니면 차단.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
REAL_TRADE_GUARD_PATH = RUNTIME_DIR / "real_trade_guard.json"


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _norm(value) in {"1", "TRUE", "YES", "Y", "ON", "ENABLED"}


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


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


def read_real_trade_guard() -> dict[str, Any]:
    data = _read_json(REAL_TRADE_GUARD_PATH, {})
    return data if isinstance(data, dict) else {}


def _read_order_queue_from_path(path: Path) -> dict[str, Any]:
    data = _read_json(path, {"version": 1, "updated_at": "", "orders": []})
    if not isinstance(data, dict):
        data = {"version": 1, "updated_at": "", "orders": []}
    if not isinstance(data.get("orders"), list):
        data["orders"] = []
    return data


def _write_order_queue_to_path(path: Path, data: dict[str, Any]) -> None:
    data["version"] = data.get("version", 1)
    data["updated_at"] = now_text()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_guard_from_path(path: Path) -> dict[str, Any]:
    data = _read_json(path, {})
    return data if isinstance(data, dict) else {}


def ensure_default_real_trade_guard() -> dict[str, Any]:
    """기본 실거래 가드 파일을 생성한다.

    기본값은 전부 False.
    """
    guard = read_real_trade_guard()
    if guard:
        return guard

    guard = {
        "version": 1,
        "updated_at": now_text(),
        "real_trade_enabled": False,
        "kiwoom_logged_in": False,
        "account_selected": False,
        "operator_confirmed": False,
        "account_no": "",
        "note": "기본값은 전부 False입니다. 실제 주문은 명시적으로 활성화해야 합니다.",
    }
    _write_json(REAL_TRADE_GUARD_PATH, guard)
    return guard


def evaluate_real_order_preflight(order: dict[str, Any], guard: dict[str, Any]) -> dict[str, Any]:
    status = _norm(order.get("status"))
    side = _norm(order.get("side"))
    quantity = _safe_int(order.get("quantity"))

    if status not in {"EXECUTABLE", "REAL_READY", "BLOCKED_REAL"}:
        return {
            "real_preflight_status": "IGNORED",
            "real_preflight_reason": f"실주문 사전검사 대상 아님: {status}",
        }

    if not _truthy(guard.get("real_trade_enabled")):
        return {
            "real_preflight_status": "BLOCKED_REAL",
            "real_preflight_reason": "실거래 허용 OFF",
        }

    if not _truthy(guard.get("kiwoom_logged_in")):
        return {
            "real_preflight_status": "BLOCKED_REAL",
            "real_preflight_reason": "키움 로그인 미확인",
        }

    if not _truthy(guard.get("account_selected")):
        return {
            "real_preflight_status": "BLOCKED_REAL",
            "real_preflight_reason": "계좌 미선택",
        }

    if not str(guard.get("account_no", "") or "").strip():
        return {
            "real_preflight_status": "BLOCKED_REAL",
            "real_preflight_reason": "계좌번호 없음",
        }

    if not _truthy(guard.get("operator_confirmed")):
        return {
            "real_preflight_status": "BLOCKED_REAL",
            "real_preflight_reason": "운영자 실주문 확인 없음",
        }

    if not _truthy(order.get("execution_enabled")):
        return {
            "real_preflight_status": "BLOCKED_REAL",
            "real_preflight_reason": "주문 execution_enabled=False",
        }

    if side not in {"BUY", "SELL"}:
        return {
            "real_preflight_status": "BLOCKED_REAL",
            "real_preflight_reason": f"지원하지 않는 주문방향: {side}",
        }

    if quantity is None or quantity <= 0:
        return {
            "real_preflight_status": "BLOCKED_REAL",
            "real_preflight_reason": "주문수량 없음 또는 0 이하",
        }

    if not str(order.get("order_type", "") or "").strip():
        return {
            "real_preflight_status": "BLOCKED_REAL",
            "real_preflight_reason": "주문유형 없음",
        }

    return {
        "real_preflight_status": "REAL_READY",
        "real_preflight_reason": "실주문 사전검사 통과",
    }


def apply_real_order_preflight() -> dict[str, Any]:
    guard = ensure_default_real_trade_guard()
    data = read_order_queue()
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []
        data["orders"] = orders

    checked = 0
    real_ready = 0
    blocked_real = 0
    ignored = 0

    for order in orders:
        if not isinstance(order, dict):
            ignored += 1
            continue

        result = evaluate_real_order_preflight(order, guard)
        preflight_status = result.get("real_preflight_status", "BLOCKED_REAL")

        if preflight_status == "IGNORED":
            ignored += 1
            continue

        checked += 1
        order["real_preflight_status"] = preflight_status
        order["real_preflight_reason"] = result.get("real_preflight_reason", "")
        order["real_preflight_checked_at"] = now_text()

        if preflight_status == "REAL_READY":
            order["status"] = "REAL_READY"
            real_ready += 1
        elif preflight_status == "BLOCKED_REAL":
            order["status"] = "BLOCKED_REAL"
            blocked_real += 1
        else:
            ignored += 1

    write_order_queue(data)

    return {
        "checked": checked,
        "real_ready": real_ready,
        "blocked_real": blocked_real,
        "ignored": ignored,
        "order_queue_path": str(ORDER_QUEUE_PATH),
        "real_trade_guard_path": str(REAL_TRADE_GUARD_PATH),
    }


def apply_real_order_preflight_for_order(
    order_id: str,
    queue_path: str | Path | None = None,
    guard_path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply real-order preflight to one EXECUTABLE order only."""
    clean_order_id = str(order_id or "").strip()
    target_queue_path = Path(queue_path) if queue_path is not None else ORDER_QUEUE_PATH
    target_guard_path = Path(guard_path) if guard_path is not None else REAL_TRADE_GUARD_PATH

    if not clean_order_id:
        return {
            "ok": False,
            "status": "skipped",
            "reason": "order_id is required",
            "order_id": clean_order_id,
            "order_queue_path": str(target_queue_path),
            "real_trade_guard_path": str(target_guard_path),
            "changed": False,
        }

    if not target_guard_path.exists():
        return {
            "ok": False,
            "status": "skipped",
            "reason": "real trade guard not found",
            "order_id": clean_order_id,
            "order_queue_path": str(target_queue_path),
            "real_trade_guard_path": str(target_guard_path),
            "changed": False,
        }

    guard = _read_guard_from_path(target_guard_path)
    if not guard:
        return {
            "ok": False,
            "status": "skipped",
            "reason": "real trade guard is empty or invalid",
            "order_id": clean_order_id,
            "order_queue_path": str(target_queue_path),
            "real_trade_guard_path": str(target_guard_path),
            "changed": False,
        }

    data = _read_order_queue_from_path(target_queue_path)
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        return {
            "ok": False,
            "status": "skipped",
            "reason": "orders must be a list",
            "order_id": clean_order_id,
            "order_queue_path": str(target_queue_path),
            "real_trade_guard_path": str(target_guard_path),
            "changed": False,
        }

    for order in orders:
        if not isinstance(order, dict):
            continue
        if str(order.get("id", "") or "").strip() != clean_order_id:
            continue

        before_status = _norm(order.get("status"))
        if before_status != "EXECUTABLE":
            return {
                "ok": True,
                "status": "skipped",
                "reason": f"target order status is not EXECUTABLE: {before_status}",
                "order_id": clean_order_id,
                "before_status": before_status,
                "after_status": before_status,
                "order_queue_path": str(target_queue_path),
                "real_trade_guard_path": str(target_guard_path),
                "changed": False,
            }

        result = evaluate_real_order_preflight(order, guard)
        preflight_status = str(result.get("real_preflight_status", "") or "").upper()
        order["real_preflight_status"] = preflight_status
        order["real_preflight_reason"] = result.get("real_preflight_reason", "")
        order["real_preflight_checked_at"] = now_text()

        if preflight_status == "REAL_READY":
            order["status"] = "REAL_READY"
        elif preflight_status == "BLOCKED_REAL":
            order["status"] = "BLOCKED_REAL"
        else:
            return {
                "ok": True,
                "status": "skipped",
                "reason": f"real preflight ignored order: {preflight_status}",
                "order_id": clean_order_id,
                "before_status": before_status,
                "after_status": before_status,
                "real_preflight_status": preflight_status,
                "order_queue_path": str(target_queue_path),
                "real_trade_guard_path": str(target_guard_path),
                "changed": False,
            }

        _write_order_queue_to_path(target_queue_path, data)
        return {
            "ok": True,
            "status": "updated",
            "reason": order.get("real_preflight_reason", ""),
            "order_id": clean_order_id,
            "before_status": before_status,
            "after_status": order.get("status", ""),
            "real_preflight_status": preflight_status,
            "execution_enabled": bool(order.get("execution_enabled", False)),
            "order_queue_path": str(target_queue_path),
            "real_trade_guard_path": str(target_guard_path),
            "changed": True,
        }

    return {
        "ok": False,
        "status": "not_found",
        "reason": "order id not found",
        "order_id": clean_order_id,
        "order_queue_path": str(target_queue_path),
        "real_trade_guard_path": str(target_guard_path),
        "changed": False,
    }


def summarize_real_order_preflight() -> dict[str, Any]:
    data = read_order_queue()
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        orders = []

    summary = {
        "path": str(ORDER_QUEUE_PATH),
        "total": len(orders),
        "real_ready": 0,
        "blocked_real": 0,
        "executable": 0,
        "other": 0,
    }

    for order in orders:
        if not isinstance(order, dict):
            continue
        status = _norm(order.get("status"))
        if status == "REAL_READY":
            summary["real_ready"] += 1
        elif status == "BLOCKED_REAL":
            summary["blocked_real"] += 1
        elif status == "EXECUTABLE":
            summary["executable"] += 1
        else:
            summary["other"] += 1

    return summary
