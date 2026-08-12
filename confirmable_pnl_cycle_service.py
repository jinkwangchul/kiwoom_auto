# -*- coding: utf-8 -*-
"""Stock-scoped confirmable cumulative PnL cycle ledger and projection."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any

from runtime_atomic_writer import write_json_atomic


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "runtime" / "pnl_cycle_boundaries.json"
_LOCK = RLock()


@dataclass(frozen=True)
class ConfirmablePnlRuntimeSnapshot:
    boundaries: tuple[dict[str, Any], ...]
    fills_data: dict[str, Any]
    realized_data: dict[str, Any]
    positions_data: dict[str, Any]
    broker_data: dict[str, Any]
    runtime_error: str = ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


def read_pnl_cycle_ledger(path: str | Path = DEFAULT_LEDGER_PATH) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"version": 1, "updated_at": "", "boundaries": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "issues": [str(exc)], "boundaries": []}
    boundaries = data.get("boundaries") if isinstance(data, dict) else None
    if not isinstance(boundaries, list) or any(not isinstance(item, dict) for item in boundaries):
        return {"ok": False, "issues": ["pnl cycle ledger schema invalid"], "boundaries": []}
    return {**data, "ok": True, "boundaries": boundaries}


def latest_pnl_cycle_boundary(stock_code: str, path: str | Path = DEFAULT_LEDGER_PATH) -> dict[str, Any] | None:
    code = _text(stock_code).lstrip("A")
    ledger = read_pnl_cycle_ledger(path)
    matches = [item for item in ledger.get("boundaries", []) if _text(item.get("stock_code")).lstrip("A") == code]
    return max(matches, key=lambda item: (_text(item.get("boundary_at")), _text(item.get("boundary_id")))) if matches else None


def _append_boundary(record: dict[str, Any], path: str | Path) -> dict[str, Any]:
    target = Path(path)
    with _LOCK:
        ledger = read_pnl_cycle_ledger(target)
        if ledger.get("ok") is False:
            return {"written": False, "blocked": True, "reasons": ledger.get("issues", [])}
        boundaries = list(ledger.get("boundaries", []))
        duplicate = next((item for item in boundaries if item.get("completion_evidence_id") == record["completion_evidence_id"] and item.get("stock_code") == record["stock_code"]), None)
        if duplicate:
            return {"written": False, "duplicate": True, "boundary": duplicate}
        boundaries.append(record)
        payload = {"version": 1, "updated_at": record["created_at"], "boundaries": boundaries}
        result = write_json_atomic(target, payload)
        ok = result.get("status") == "OK" and result.get("written") is True
        return {"written": ok, "duplicate": False, "boundary": record, "write_result": result}


def record_completion_boundaries(completion_result: dict[str, Any], *, ledger_path: str | Path = DEFAULT_LEDGER_PATH) -> list[dict[str, Any]]:
    if completion_result.get("normal_ended_applied") is not True:
        return []
    evaluated = completion_result.get("evaluator_result")
    if not isinstance(evaluated, dict) or evaluated.get("global_complete") is not True:
        return []
    ended_at = _text((completion_result.get("normal_end_write") or {}).get("operation_ended_at"))
    if not ended_at:
        return []
    outputs = []
    for item in evaluated.get("stock_results") or []:
        if not isinstance(item, dict) or _text(item.get("status")).upper() != "DONE":
            continue  # CARRYOVER_DONE is intentionally excluded.
        if int(item.get("holding_qty") or 0) != 0 or int(item.get("pending_buy_qty") or 0) != 0 or int(item.get("pending_sell_qty") or 0) != 0 or item.get("active_order_ids"):
            continue
        code = _text(item.get("stock_code")).lstrip("A")
        evidence_id = hashlib.sha256(f"{ended_at}|{code}|{_text(item.get('close_mode'))}".encode("utf-8")).hexdigest().upper()
        record = {
            "stock_code": code,
            "boundary_id": f"PNL-BOUNDARY-{evidence_id[:20]}",
            "boundary_at": ended_at,
            "boundary_reason": "OFFICIAL_CLOSE_COMPLETED",
            "completion_mode": _text(item.get("close_mode")),
            "completion_evidence_id": evidence_id,
            "bootstrap": False,
            "created_at": ended_at,
        }
        outputs.append(_append_boundary(record, ledger_path))
    return outputs


def bootstrap_pnl_cycle(stock_code: str, *, clean_integrity_confirmed: bool, evidence: dict[str, Any], boundary_at: str | None = None, ledger_path: str | Path = DEFAULT_LEDGER_PATH) -> dict[str, Any]:
    code = _text(stock_code).lstrip("A")
    blockers = [key for key in ("holding_qty", "pending_buy_qty", "pending_sell_qty", "pending_cancel_count", "active_close_liquidation_count") if int(evidence.get(key) or 0) != 0]
    if not clean_integrity_confirmed or _text(evidence.get("recovery_status")).upper() not in {"READY", "PASSED"} or _text(evidence.get("reconciliation_status")).upper() not in {"CONSISTENT", "PASSED"}:
        blockers.append("INTEGRITY_NOT_CONFIRMED")
    if latest_pnl_cycle_boundary(code, ledger_path):
        return {"written": False, "blocked": True, "reasons": ["BOUNDARY_ALREADY_EXISTS"]}
    if blockers:
        return {"written": False, "blocked": True, "review_required": True, "reasons": blockers}
    when = boundary_at or datetime.now().astimezone().isoformat(timespec="seconds")
    evidence_id = hashlib.sha256(f"BOOTSTRAP|{code}|{when}".encode()).hexdigest().upper()
    return _append_boundary({"stock_code": code, "boundary_id": f"PNL-BOOTSTRAP-{evidence_id[:20]}", "boundary_at": when, "boundary_reason": "CLEAN_RUNTIME_BOOTSTRAP", "completion_mode": "", "completion_evidence_id": evidence_id, "bootstrap": True, "created_at": when}, ledger_path)


def load_confirmable_pnl_runtime_snapshot(
    *,
    project_root: str | Path = PROJECT_ROOT,
    ledger_path: str | Path | None = None,
) -> ConfirmablePnlRuntimeSnapshot:
    root = Path(project_root)
    ledger = read_pnl_cycle_ledger(
        ledger_path or root / "runtime" / "pnl_cycle_boundaries.json"
    )

    def load_runtime(name: str, default: dict[str, Any]) -> dict[str, Any]:
        path = root / "runtime" / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    try:
        fills_data = load_runtime("fills.json", {"fills": []})
        realized_data = load_runtime("realized_pnl.json", {"records": []})
        positions_data = load_runtime("positions.json", {"positions": []})
        broker_data = load_runtime("broker_holdings.json", {"holdings": []})
        runtime_error = ""
    except Exception as exc:
        fills_data = {"fills": []}
        realized_data = {"records": []}
        positions_data = {"positions": []}
        broker_data = {"holdings": []}
        runtime_error = str(exc)
    boundaries = ledger.get("boundaries", [])
    if not isinstance(boundaries, list):
        boundaries = []
    return ConfirmablePnlRuntimeSnapshot(
        boundaries=tuple(item for item in boundaries if isinstance(item, dict)),
        fills_data=fills_data,
        realized_data=realized_data,
        positions_data=positions_data,
        broker_data=broker_data,
        runtime_error=runtime_error,
    )


def project_confirmable_cumulative_pnl_from_snapshot(
    stock_code: str,
    evaluation_price: Any,
    snapshot: ConfirmablePnlRuntimeSnapshot,
) -> dict[str, Any]:
    code = _text(stock_code).lstrip("A")
    matching_boundaries = [
        item
        for item in snapshot.boundaries
        if _text(item.get("stock_code")).lstrip("A") == code
    ]
    boundary = (
        max(
            matching_boundaries,
            key=lambda item: (
                _text(item.get("boundary_at")),
                _text(item.get("boundary_id")),
            ),
        )
        if matching_boundaries
        else None
    )
    unavailable = {"available": False, "realized_profit": None, "unrealized_profit": None, "cumulative_profit": None, "cumulative_rate": None, "completed_buy_cost": None, "open_cost": None, "boundary_id": _text((boundary or {}).get("boundary_id")), "evaluation_price": evaluation_price, "reconciliation_status": "UNAVAILABLE"}
    if not boundary:
        return {**unavailable, "reason": "PNL_CYCLE_BOOTSTRAP_REQUIRED"}
    price = _decimal(evaluation_price)
    if price is None or price <= 0:
        return {**unavailable, "reason": "EVALUATION_PRICE_UNAVAILABLE"}
    if snapshot.runtime_error:
        return {
            **unavailable,
            "reason": f"RUNTIME_EVIDENCE_UNAVAILABLE:{snapshot.runtime_error}",
        }
    fills_data = snapshot.fills_data
    realized_data = snapshot.realized_data
    positions_data = snapshot.positions_data
    broker_data = snapshot.broker_data
    boundary_at = _text(boundary.get("boundary_at"))
    fills = [item for item in fills_data.get("fills", []) if isinstance(item, dict) and _text(item.get("code")).lstrip("A") == code and _text(item.get("received_at") or item.get("recorded_at")) > boundary_at]
    fills.sort(key=lambda item: (_text(item.get("received_at") or item.get("recorded_at")), _text(item.get("fill_id"))))
    inventory_qty = 0
    inventory_cost = Decimal("0")
    completed_cost = Decimal("0")
    seen: set[str] = set()
    cumulative_by_order: dict[str, int] = {}
    for fill in fills:
        fid = _text(fill.get("fill_id")); order_key = _text(fill.get("broker_order_no") or fill.get("order_id")); cumulative = int(fill.get("filled_quantity") or 0)
        if not fid or fid in seen or not order_key or cumulative <= cumulative_by_order.get(order_key, 0):
            continue
        qty = cumulative - cumulative_by_order.get(order_key, 0); cumulative_by_order[order_key] = cumulative; seen.add(fid)
        fill_price = _decimal(fill.get("filled_price"))
        if fill_price is None or fill_price <= 0:
            return {**unavailable, "reason": "FILL_PRICE_INVALID"}
        if _text(fill.get("side")).upper() == "BUY":
            inventory_qty += qty; inventory_cost += fill_price * qty
        elif _text(fill.get("side")).upper() == "SELL":
            if inventory_qty <= 0 or qty > inventory_qty:
                return {**unavailable, "reason": "BUY_COST_HISTORY_INCOMPLETE"}
            matched = inventory_cost * Decimal(qty) / Decimal(inventory_qty); completed_cost += matched; inventory_qty -= qty; inventory_cost -= matched
    positions = [item for item in positions_data.get("positions", []) if isinstance(item, dict) and _text(item.get("code")).lstrip("A") == code]
    brokers = [item for item in (broker_data.get("holdings") or broker_data.get("broker_holdings") or []) if isinstance(item, dict) and _text(item.get("code") or item.get("stock_code")).lstrip("A") == code]
    if len(positions) > 1 or len(brokers) > 1:
        return {**unavailable, "reason": "RECONCILIATION_DUPLICATE"}
    position = positions[0] if positions else {}; broker = brokers[0] if brokers else {}
    qty = int(position.get("quantity") or 0); broker_qty = int(broker.get("quantity") or broker.get("holding_quantity") or broker.get("holding_qty") or 0)
    average = _decimal(position.get("average_price")) or Decimal("0"); open_cost = _decimal(position.get("cost_basis")) or average * qty
    if qty != broker_qty or inventory_qty != qty or abs(inventory_cost - open_cost) > Decimal("0.01"):
        return {**unavailable, "reason": "BROKER_RUNTIME_RECONCILIATION_MISMATCH"}
    realized_items = realized_data.get("records") if isinstance(realized_data.get("records"), list) else realized_data.get("realizations", [])
    records = [item for item in realized_items if isinstance(item, dict) and _text(item.get("stock_code")).lstrip("A") == code and _text(item.get("realized_at") or item.get("trade_date")) >= boundary_at[:10]]
    realized = sum((_decimal(item.get("gross_realized_profit")) or Decimal("0") for item in records), Decimal("0"))
    unrealized = (price - average) * qty if qty else Decimal("0"); cumulative = realized + unrealized; denominator = completed_cost + open_cost
    return {"available": True, "reason": "" if denominator > 0 else "ZERO_COST_DENOMINATOR", "realized_profit": _number(realized), "unrealized_profit": _number(unrealized), "cumulative_profit": _number(cumulative), "cumulative_rate": _number(cumulative / denominator * 100) if denominator > 0 else None, "completed_buy_cost": _number(completed_cost), "open_cost": _number(open_cost), "boundary_id": boundary["boundary_id"], "boundary_at": boundary_at, "evaluation_price": _number(price), "reconciliation_status": "CONSISTENT"}


def project_confirmable_cumulative_pnl(stock_code: str, evaluation_price: Any, *, project_root: str | Path = PROJECT_ROOT, ledger_path: str | Path | None = None) -> dict[str, Any]:
    snapshot = load_confirmable_pnl_runtime_snapshot(
        project_root=project_root,
        ledger_path=ledger_path,
    )
    return project_confirmable_cumulative_pnl_from_snapshot(
        stock_code,
        evaluation_price,
        snapshot,
    )
