# -*- coding: utf-8 -*-
"""Dry-run bridge from routine signal queue to OrderManager.

This module reads runtime/routine_signals.json and asks OrderManager for a
decision with order_executor=None. It never sends orders and never mutates
routine_signals.json, orders.json, state.json, or rules.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from order_queue import signal_to_order_candidate
from order_manager import handle_routine_signal_for_stock_dir
from stock_repository import StockRepository


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
SIGNAL_QUEUE_PATH = RUNTIME_DIR / "routine_signals.json"
VALID_SIGNALS = {"BUY", "SELL"}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_upper(value: Any) -> str:
    return _normalize_text(value).upper()


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _is_false(value: Any) -> bool:
    if isinstance(value, bool):
        return value is False
    return str(value).strip().lower() in {"", "0", "false", "no", "n", "off"}


def load_pending_routine_signals() -> list[dict[str, Any]]:
    """Return PENDING BUY/SELL signals that are explicitly execution-disabled."""
    data = _read_json(SIGNAL_QUEUE_PATH, {"signals": []})
    signals = data.get("signals", []) if isinstance(data, dict) else []
    if not isinstance(signals, list):
        return []

    pending: list[dict[str, Any]] = []
    for record in signals:
        if not isinstance(record, dict):
            continue
        if _normalize_upper(record.get("status")) != "PENDING":
            continue
        if _normalize_upper(record.get("signal")) not in VALID_SIGNALS:
            continue
        if not _is_false(record.get("execution_enabled")):
            continue
        pending.append(record)
    return pending


def resolve_stock_dir_from_signal(signal_record: dict[str, Any]) -> Path | None:
    """Resolve a stock runtime directory from a routine signal record."""
    if not isinstance(signal_record, dict):
        return None

    code = _normalize_text(signal_record.get("code"))
    name = _normalize_text(signal_record.get("name"))
    if not code:
        return None

    try:
        stock_dir = StockRepository().resolve_stock_dir(code, name)
    except Exception:
        stock_dir = PROJECT_ROOT / "stocks" / (f"{code}_{name}" if name else code)

    return stock_dir if stock_dir.exists() else None


def dry_run_order_manager_for_signal(signal_record: dict[str, Any]) -> dict[str, Any]:
    """Run OrderManager decision for one signal without an order executor."""
    if not isinstance(signal_record, dict):
        return {
            "ok": False,
            "reason": "signal_record must be a dict",
            "order_executor_called": False,
        }

    signal_type = _normalize_upper(signal_record.get("signal"))
    if signal_type not in VALID_SIGNALS:
        return {
            "ok": False,
            "reason": f"unsupported signal: {signal_type}",
            "signal_id": signal_record.get("id", ""),
            "order_executor_called": False,
        }

    stock_dir = resolve_stock_dir_from_signal(signal_record)
    if stock_dir is None:
        return {
            "ok": False,
            "reason": "stock directory not found",
            "signal_id": signal_record.get("id", ""),
            "code": signal_record.get("code", ""),
            "name": signal_record.get("name", ""),
            "signal_type": signal_type,
            "order_executor_called": False,
        }

    decision = handle_routine_signal_for_stock_dir(
        stock_dir,
        signal_type,
        source="routine_signal_order_bridge",
        order_executor=None,
    )
    result = dict(decision) if isinstance(decision, dict) else {"decision": decision}
    result.update(
        {
            "ok": True,
            "signal_id": signal_record.get("id", ""),
            "code": signal_record.get("code", ""),
            "name": signal_record.get("name", ""),
            "signal_type": signal_type,
            "stock_dir": str(stock_dir),
            "queue_status_unchanged": True,
            "files_mutated": False,
        }
    )
    result["order_executor_called"] = bool(result.get("order_executor_called", False))
    return result


def build_order_payload_preview_for_signal(signal_record: dict[str, Any]) -> dict[str, Any]:
    """Build an in-memory order payload candidate without writing order_queue.json."""
    if not isinstance(signal_record, dict):
        return {
            "payload_preview_ok": False,
            "reason": "signal_record must be a dict",
            "execution_enabled": False,
            "not_saved": True,
            "order_queue_written": False,
            "send_order_called": False,
        }

    try:
        candidate = signal_to_order_candidate(signal_record, index=0)
    except Exception as exc:
        return {
            "payload_preview_ok": False,
            "reason": f"payload preview failed: {exc}",
            "signal_id": signal_record.get("id", ""),
            "signal_type": _normalize_upper(signal_record.get("signal")),
            "execution_enabled": False,
            "not_saved": True,
            "order_queue_written": False,
            "send_order_called": False,
        }

    if candidate is None:
        return {
            "payload_preview_ok": False,
            "reason": "signal cannot be converted to an order candidate",
            "signal_id": signal_record.get("id", ""),
            "signal_type": _normalize_upper(signal_record.get("signal")),
            "execution_enabled": False,
            "not_saved": True,
            "order_queue_written": False,
            "send_order_called": False,
        }

    preview = dict(candidate)
    preview["execution_enabled"] = False
    preview["payload_preview_ok"] = True
    preview["not_saved"] = True
    preview["order_queue_written"] = False
    preview["send_order_called"] = False
    return preview


def dry_run_order_manager_for_signal_with_payload_preview(
    signal_record: dict[str, Any],
) -> dict[str, Any]:
    """Run OrderManager dry-run and build an order payload preview for one signal."""
    order_manager_result = dry_run_order_manager_for_signal(signal_record)
    payload_preview = build_order_payload_preview_for_signal(signal_record)

    return {
        "signal_id": signal_record.get("id", "") if isinstance(signal_record, dict) else "",
        "code": signal_record.get("code", "") if isinstance(signal_record, dict) else "",
        "name": signal_record.get("name", "") if isinstance(signal_record, dict) else "",
        "signal_type": _normalize_upper(signal_record.get("signal")) if isinstance(signal_record, dict) else "",
        "order_manager": order_manager_result,
        "payload_preview": payload_preview,
        "payload_built": bool(payload_preview.get("payload_preview_ok")),
        "payload_candidate_status": payload_preview.get("candidate_status", ""),
        "order_manager_allowed": bool(order_manager_result.get("allowed")),
        "send_order_called": False,
        "not_saved": True,
        "order_queue_written": False,
        "files_mutated": False,
    }


def dry_run_pending_routine_signals(limit: int | None = None) -> dict[str, Any]:
    """Dry-run all pending routine signals through OrderManager decision only."""
    signals = load_pending_routine_signals()
    try:
        clean_limit = int(limit) if limit is not None else None
    except (TypeError, ValueError):
        clean_limit = None
    if clean_limit is not None and clean_limit >= 0:
        signals = signals[:clean_limit]

    results = [dry_run_order_manager_for_signal(signal) for signal in signals]
    allowed = sum(1 for item in results if bool(item.get("allowed")))
    blocked = sum(1 for item in results if item.get("ok") and not bool(item.get("allowed")))
    errors = sum(1 for item in results if not item.get("ok"))

    return {
        "summary": {
            "signals_checked": len(signals),
            "allowed": allowed,
            "blocked": blocked,
            "errors": errors,
            "order_executor_called": any(bool(item.get("order_executor_called")) for item in results),
            "queue_status_changed": False,
            "files_mutated": False,
        },
        "results": results,
    }


def dry_run_pending_routine_signals_with_payload_preview(limit: int | None = None) -> dict[str, Any]:
    """Dry-run pending routine signals and include in-memory order payload previews."""
    signals = load_pending_routine_signals()
    try:
        clean_limit = int(limit) if limit is not None else None
    except (TypeError, ValueError):
        clean_limit = None
    if clean_limit is not None and clean_limit >= 0:
        signals = signals[:clean_limit]

    results = [
        dry_run_order_manager_for_signal_with_payload_preview(signal)
        for signal in signals
    ]
    allowed = sum(1 for item in results if bool(item.get("order_manager_allowed")))
    blocked = sum(
        1
        for item in results
        if item.get("order_manager", {}).get("ok") and not bool(item.get("order_manager_allowed"))
    )
    errors = sum(
        1
        for item in results
        if not item.get("order_manager", {}).get("ok") or not item.get("payload_built")
    )

    return {
        "summary": {
            "signals_checked": len(signals),
            "payloads_built": sum(1 for item in results if bool(item.get("payload_built"))),
            "allowed": allowed,
            "blocked": blocked,
            "errors": errors,
            "send_order_called": False,
            "files_mutated": False,
        },
        "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(dry_run_pending_routine_signals(), ensure_ascii=False, indent=2))
