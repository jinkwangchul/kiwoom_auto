# -*- coding: utf-8 -*-
"""Shared lightweight current-price PnL refresh contract for GUI surfaces."""
from pathlib import Path
from typing import Any
from confirmable_pnl_cycle_service import project_confirmable_cumulative_pnl
from gui_review_utils import current_price_from_state
from runtime_io import read_json_dict
from stock_repository import StockRepository

PNL_REFRESH_INTERVAL_MS = 1_000

def project_current_stock_pnl(stock_code: str, *, project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root)
    state = read_json_dict(StockRepository(project_root=root).resolve_stock_dir(stock_code) / "state.json")
    result = project_confirmable_cumulative_pnl(stock_code, current_price_from_state(state), project_root=root)
    result["evaluation_price_at"] = str(state.get("current_price_updated_at") or state.get("last_checked_at") or state.get("updated_at") or "").strip()
    return result
