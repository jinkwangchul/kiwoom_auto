"""Shared preflight policy for Group and RoutineInstance deletion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from gui_auto_trade_integrity import (
    is_review_protected_stock_dir,
    is_review_required_state,
)
from runtime_io import read_json_dict
from stock_repository import StockRecord


DELETE_BLOCKED_OPERATION_RUNNING = "DELETE_BLOCKED_OPERATION_RUNNING"
DELETE_BLOCKED_LONG_TERM_HOLDING = "DELETE_BLOCKED_LONG_TERM_HOLDING"
DELETE_BLOCKED_STATE_UNAVAILABLE = "DELETE_BLOCKED_STATE_UNAVAILABLE"


@dataclass(frozen=True)
class DeleteScopeBlock:
    code: str
    name: str
    reason_code: str
    message: str


def preview_delete_scope(
    project_root: Path | str,
    stocks: Iterable[StockRecord],
    *,
    running_stock_dirs: Iterable[Path | str] = (),
) -> tuple[DeleteScopeBlock, ...]:
    """Return fail-closed blockers without mutating assignment or runtime data."""
    root = Path(project_root).resolve(strict=False)
    running = {
        str(Path(path).resolve(strict=False)) for path in running_stock_dirs
    }
    blockers: list[DeleteScopeBlock] = []
    for stock in stocks:
        stock_dir = Path(stock.stock_path)
        if not stock_dir.is_absolute():
            stock_dir = root / stock_dir
        resolved = stock_dir.resolve(strict=False)
        if str(resolved) in running:
            blockers.append(
                DeleteScopeBlock(
                    stock.code,
                    stock.name,
                    DELETE_BLOCKED_OPERATION_RUNNING,
                    f"{stock.name}: 운영 중",
                )
            )
            continue

        state_path = resolved / "state.json"
        if not state_path.is_file():
            blockers.append(
                DeleteScopeBlock(
                    stock.code,
                    stock.name,
                    DELETE_BLOCKED_STATE_UNAVAILABLE,
                    f"{stock.name}: 현재 상태 확인 불가",
                )
            )
            continue
        state = read_json_dict(state_path)
        try:
            holding_qty = int(state.get("holding_qty", 0) or 0)
        except (TypeError, ValueError):
            holding_qty = 0
        review_required = bool(
            is_review_required_state(state)
            or is_review_protected_stock_dir(resolved)
        )
        if holding_qty > 0 and not review_required:
            blockers.append(
                DeleteScopeBlock(
                    stock.code,
                    stock.name,
                    DELETE_BLOCKED_LONG_TERM_HOLDING,
                    f"{stock.name}: 장기보유 {holding_qty}주",
                )
            )
    return tuple(blockers)
