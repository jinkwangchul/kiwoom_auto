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

from copy import deepcopy
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

try:
    from execution_queue_writer import mutate_order_queue
except Exception:  # pragma: no cover
    mutate_order_queue = None


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


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


def write_order_queue(data: dict[str, Any]) -> dict[str, Any]:
    return replace_order_queue(data)


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


def _source_signal_id(order: dict[str, Any]) -> str:
    return str(order.get("source_signal_id", "") or "").strip()


def _queue_result(
    *,
    ok: bool,
    write_stage: str,
    reason: str = "",
    orders_created: int = 0,
    duplicates: int = 0,
    ignored: int = 0,
    created_orders: list[dict[str, Any]] | None = None,
    duplicate_orders: list[dict[str, Any]] | None = None,
    mutation_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": ok,
        "write_stage": write_stage,
        "reason": reason,
        "orders_created": orders_created,
        "duplicates": duplicates,
        "ignored": ignored,
        "order_queue_written": bool(_as_bool(mutation_result, "queue_write")),
        "created_orders": deepcopy(created_orders or []),
        "duplicate_orders": deepcopy(duplicate_orders or []),
        "order_queue_path": str(ORDER_QUEUE_PATH),
    }
    if isinstance(mutation_result, dict):
        for key in (
            "committed",
            "changed",
            "file_write",
            "queue_write",
            "queue_committed",
            "post_write_verified",
            "revision_before",
            "revision_after",
            "expected_revision",
            "cas_checked",
            "lock_acquired",
            "lock_wait_ms",
            "backup_path",
            "blocked_reasons",
            "warnings",
        ):
            result[key] = deepcopy(mutation_result.get(key))
    return result


def _as_bool(value: Any, key: str) -> bool:
    return isinstance(value, dict) and value.get(key) is True


def _initial_order_queue() -> dict[str, Any]:
    return {"version": 1, "revision": 0, "updated_at": "", "orders": []}


def _candidate_duplicate_reason(order: dict[str, Any], orders: list[Any]) -> str | None:
    source_signal_id = _source_signal_id(order)
    if source_signal_id:
        for existing in orders:
            if isinstance(existing, dict) and _source_signal_id(existing) == source_signal_id:
                return "duplicate source_signal_id"

    key = _order_dedupe_key(order)
    if key.strip("|"):
        for existing in orders:
            if isinstance(existing, dict) and _order_dedupe_key(existing) == key:
                return "duplicate legacy candidate key"
    return None


def append_order_candidates(
    candidates: list[dict[str, Any]],
    *,
    backup: bool = True,
    context: dict[str, Any] | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Append legacy PENDING/APPROVED/BLOCKED candidates via the canonical writer."""
    if not callable(mutate_order_queue):
        return _queue_result(
            ok=False,
            write_stage="canonical_writer",
            reason="execution_queue_writer.mutate_order_queue unavailable",
            ignored=len(candidates),
        )

    valid_candidates = [deepcopy(item) for item in candidates if isinstance(item, dict)]
    ignored = len(candidates) - len(valid_candidates)
    if not valid_candidates:
        return _queue_result(ok=True, write_stage="candidate_append_noop", ignored=ignored)

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        orders = data.get("orders")
        if not isinstance(orders, list):
            orders = []
            data["orders"] = orders

        created: list[dict[str, Any]] = []
        duplicates_found: list[dict[str, Any]] = []
        updated_data = deepcopy(data)
        updated_orders = updated_data.get("orders")
        if not isinstance(updated_orders, list):
            updated_orders = []
            updated_data["orders"] = updated_orders

        for candidate in valid_candidates:
            reason = _candidate_duplicate_reason(candidate, updated_orders)
            if reason:
                duplicate = deepcopy(candidate)
                duplicate["duplicate_reason"] = reason
                duplicates_found.append(duplicate)
                continue
            updated_orders.append(deepcopy(candidate))
            created.append(deepcopy(candidate))

        if not created:
            return {
                "blocked": {
                    "committed": False,
                    "write_stage": "duplicate",
                    "next_stage": "BLOCKED",
                    "changed": False,
                    "blocked_reasons": ["duplicate legacy candidate"],
                    "warnings": [],
                    "legacy_duplicate_noop": True,
                    "legacy_duplicates": len(duplicates_found),
                    "legacy_duplicate_orders": duplicates_found,
                }
            }

        return {
            "data": updated_data,
            "result": {
                "legacy_candidates_created": len(created),
                "legacy_duplicates": len(duplicates_found),
                "legacy_created_orders": created,
                "legacy_duplicate_orders": duplicates_found,
            },
        }

    def verify(after_data: dict[str, Any], mutation: dict[str, Any]) -> dict[str, Any] | None:
        result = mutation.get("result")
        created = result.get("legacy_created_orders", []) if isinstance(result, dict) else []
        after_orders = after_data.get("orders", [])
        if not isinstance(after_orders, list):
            return {"write_stage": "legacy_candidate_verify", "blocked_reasons": ["orders must be a list after append"]}
        for candidate in created:
            if _candidate_duplicate_reason(candidate, [item for item in after_orders if item is not candidate]) is None:
                continue
            matches = [
                item for item in after_orders
                if isinstance(item, dict)
                and _source_signal_id(item)
                and _source_signal_id(item) == _source_signal_id(candidate)
            ]
            if _source_signal_id(candidate) and len(matches) != 1:
                return {
                    "write_stage": "legacy_candidate_verify",
                    "blocked_reasons": ["appended source_signal_id must appear exactly once"],
                }
        return None

    mutation_result = mutate_order_queue(
        ORDER_QUEUE_PATH,
        mutate,
        operation_name="legacy_order_candidate_append",
        success_stage="legacy_order_candidates_appended",
        next_stage="LEGACY_ORDER_CANDIDATE_REVIEW_REQUIRED",
        backup=backup,
        context=context or {"manual_queue_write_confirmed": True},
        expected_revision=expected_revision,
        verify=verify,
        default_queue=_initial_order_queue(),
    )

    created_orders = mutation_result.get("legacy_created_orders", []) if isinstance(mutation_result, dict) else []
    duplicate_orders = mutation_result.get("legacy_duplicate_orders", []) if isinstance(mutation_result, dict) else []
    duplicate_count = int(mutation_result.get("legacy_duplicates", 0) or 0) if isinstance(mutation_result, dict) else 0

    if mutation_result.get("legacy_duplicate_noop") is True:
        return _queue_result(
            ok=True,
            write_stage="duplicate",
            reason="duplicate legacy candidate",
            duplicates=duplicate_count,
            ignored=ignored,
            duplicate_orders=duplicate_orders,
            mutation_result=mutation_result,
        )

    if mutation_result.get("committed") is True and mutation_result.get("post_write_verified") is True:
        return _queue_result(
            ok=True,
            write_stage="legacy_order_candidates_appended",
            orders_created=len(created_orders),
            duplicates=duplicate_count,
            ignored=ignored,
            created_orders=created_orders,
            duplicate_orders=duplicate_orders,
            mutation_result=mutation_result,
        )

    return _queue_result(
        ok=False,
        write_stage=str(mutation_result.get("write_stage", "legacy_order_candidate_append")),
        reason="; ".join(str(item) for item in mutation_result.get("blocked_reasons", []) if item),
        duplicates=duplicate_count,
        ignored=ignored,
        duplicate_orders=duplicate_orders,
        mutation_result=mutation_result,
    )


def replace_order_queue(
    data: dict[str, Any],
    *,
    backup: bool = True,
    context: dict[str, Any] | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Replace the legacy order queue through the canonical writer boundary."""
    if not callable(mutate_order_queue):
        return _queue_result(ok=False, write_stage="canonical_writer", reason="execution_queue_writer.mutate_order_queue unavailable")
    ctx = context if isinstance(context, dict) else {}
    if expected_revision is None or ctx.get("allow_full_queue_replace") is not True:
        return _queue_result(
            ok=False,
            write_stage="full_replace_blocked",
            reason="full order_queue replace requires allow_full_queue_replace and expected_revision",
        )
    replacement = deepcopy(data) if isinstance(data, dict) else _initial_order_queue()
    if not isinstance(replacement.get("orders"), list):
        replacement["orders"] = []

    def mutate(_: dict[str, Any]) -> dict[str, Any]:
        return {"data": deepcopy(replacement)}

    result = mutate_order_queue(
        ORDER_QUEUE_PATH,
        mutate,
        operation_name="legacy_order_queue_replace",
        success_stage="legacy_order_queue_replaced",
        next_stage="LEGACY_ORDER_QUEUE_REVIEW_REQUIRED",
        backup=backup,
        context={**ctx, "manual_queue_write_confirmed": True},
        expected_revision=expected_revision,
        default_queue=_initial_order_queue(),
    )
    result["ok"] = result.get("committed") is True and result.get("post_write_verified") is True
    return result


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

    signals = signal_data.get("signals", [])

    if not isinstance(signals, list):
        signals = []

    ignored = 0
    new_candidates: list[dict[str, Any]] = []

    for signal in signals:
        if not isinstance(signal, dict):
            ignored += 1
            continue

        order = signal_to_order_candidate(signal, len(new_candidates) + 1)
        if order is None:
            ignored += 1
            continue

        new_candidates.append(order)

    append_result = append_order_candidates(new_candidates) if new_candidates else _queue_result(
        ok=True,
        write_stage="candidate_append_noop",
        ignored=ignored,
    )

    return {
        "signals_checked": len(signals),
        "orders_created": int(append_result.get("orders_created", 0) or 0),
        "duplicates": int(append_result.get("duplicates", 0) or 0),
        "ignored": ignored,
        "order_queue_path": str(ORDER_QUEUE_PATH),
        "order_queue_written": bool(append_result.get("order_queue_written")),
        "append_result": append_result,
        "committed": append_result.get("committed"),
        "changed": append_result.get("changed"),
        "file_write": append_result.get("file_write"),
        "queue_write": append_result.get("queue_write"),
        "queue_committed": append_result.get("queue_committed"),
        "post_write_verified": append_result.get("post_write_verified"),
        "revision_before": append_result.get("revision_before"),
        "revision_after": append_result.get("revision_after"),
        "expected_revision": append_result.get("expected_revision"),
        "cas_checked": append_result.get("cas_checked"),
        "lock_acquired": append_result.get("lock_acquired"),
        "lock_wait_ms": append_result.get("lock_wait_ms"),
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
