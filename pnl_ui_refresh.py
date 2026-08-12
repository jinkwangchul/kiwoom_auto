# -*- coding: utf-8 -*-
"""Shared lightweight current-price PnL refresh contract for GUI surfaces."""
from pathlib import Path
from typing import Any, Iterable
from confirmable_pnl_cycle_service import (
    ConfirmablePnlRuntimeSnapshot,
    load_confirmable_pnl_runtime_snapshot,
    project_confirmable_cumulative_pnl_from_snapshot,
)
from gui_review_utils import current_price_from_state
from runtime_io import read_json_dict
from stock_repository import StockRepository, safe_stock_folder_name

PNL_REFRESH_INTERVAL_MS = 1_000


def _project_current_stock_pnl_from_state(
    stock_code: str,
    state: dict[str, object],
    snapshot: ConfirmablePnlRuntimeSnapshot,
) -> dict[str, Any]:
    evaluation_price = current_price_from_state(state)
    result = project_confirmable_cumulative_pnl_from_snapshot(
        stock_code,
        evaluation_price,
        snapshot,
    )
    result["evaluation_price_at"] = str(
        state.get("current_price_updated_at")
        or state.get("last_checked_at")
        or state.get("updated_at")
        or ""
    ).strip()
    return result


def _projection_error_result(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "realized_profit": None,
        "unrealized_profit": None,
        "cumulative_profit": None,
        "cumulative_rate": None,
        "completed_buy_cost": None,
        "open_cost": None,
        "boundary_id": "",
        "evaluation_price": None,
        "evaluation_price_at": "",
        "reconciliation_status": "UNAVAILABLE",
        "reason": reason,
    }


def project_current_stock_pnl_snapshot(
    stock_codes: Iterable[str],
    *,
    project_root: str | Path,
) -> dict[str, dict[str, Any]]:
    root = Path(project_root)
    codes = list(
        dict.fromkeys(
            str(code or "").strip().lstrip("A")
            for code in stock_codes
            if str(code or "").strip().lstrip("A")
        )
    )
    if not codes:
        return {}
    try:
        snapshot = load_confirmable_pnl_runtime_snapshot(project_root=root)
    except Exception as exc:
        reason = f"PNL_SNAPSHOT_UNAVAILABLE:{exc}"
        return {code: _projection_error_result(reason) for code in codes}

    repository = StockRepository(project_root=root)
    stock_dirs: dict[str, Path] = {}
    try:
        for stock_dir in repository.list_stock_dirs():
            code, _name = repository.parse_stock_folder(stock_dir)
            stock_dirs.setdefault(code, stock_dir)
    except Exception:
        stock_dirs = {}

    results: dict[str, dict[str, Any]] = {}
    for code in codes:
        try:
            stock_dir = stock_dirs.get(code) or (
                repository.stocks_dir / safe_stock_folder_name(code)
            )
            state = read_json_dict(stock_dir / "state.json")
            results[code] = _project_current_stock_pnl_from_state(
                code,
                state,
                snapshot,
            )
        except Exception as exc:
            results[code] = _projection_error_result(
                f"PNL_STOCK_PROJECTION_ERROR:{exc}"
            )
    return results


def project_current_stock_pnl(stock_code: str, *, project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root)
    state = read_json_dict(StockRepository(project_root=root).resolve_stock_dir(stock_code) / "state.json")
    snapshot = load_confirmable_pnl_runtime_snapshot(project_root=root)
    return _project_current_stock_pnl_from_state(stock_code, state, snapshot)
