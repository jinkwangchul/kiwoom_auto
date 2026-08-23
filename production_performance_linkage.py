"""Production bridge from confirmed fills to canonical performance evidence."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import threading
from typing import Any

from assignment_episode_linkage import ensure_current_assignment_episode
from assignment_episode_repository import CanonicalAssignmentEpisodeRepository
from entry_lot_repository import CanonicalEntryLotRepository
from performance_ledger_repository import (
    CANONICAL_OWNER_POLICY,
    CanonicalStockPerformanceLedgerRepository,
)
from realized_pnl_ledger import canonical_realization_id
from stock_repository import StockRepository, read_json_dict


PROJECT_ROOT = Path(__file__).resolve().parent
_LINKAGE_LOCK = threading.RLock()


def _text(value: object) -> str:
    return str(value or "").strip()


def _failure(stage: str, error: str, **extra: object) -> dict[str, Any]:
    return {
        "success": False,
        "stage": stage,
        "error": error,
        "blocked_reasons": [error],
        **extra,
    }


def _stock_config(project_root: Path, stock_code: str) -> dict[str, Any]:
    repository = StockRepository(project_root)
    stock = repository.find_by_code(stock_code)
    if stock is None:
        raise ValueError(f"Stock assignment record does not exist: {stock_code}")
    path = project_root / stock.stock_path / "config.json"
    config = read_json_dict(path)
    if not config:
        raise ValueError(f"Stock config cannot be read: {stock_code}")
    return config


def _current_episode(project_root: Path, fill: dict[str, Any]):
    stock_code = _text(fill.get("code"))
    config = _stock_config(project_root, stock_code)
    return ensure_current_assignment_episode(
        project_root,
        stock_code,
        config,
        observed_at=datetime.now().astimezone(),
        source="PRODUCTION_FILL_LINKAGE",
    )


def record_buy_entry_lot(
    project_root: Path | str,
    fill_record: dict[str, Any],
    position_result: dict[str, Any],
) -> dict[str, Any]:
    """Persist immutable BUY ownership after fill and position evidence exist."""
    fill = dict(fill_record)
    if _text(fill.get("side")).upper() != "BUY":
        return _failure("NOT_BUY", "BUY entry-lot linkage is not applicable")
    with _LINKAGE_LOCK:
        try:
            root = Path(project_root)
            repository = CanonicalEntryLotRepository(root)
            code = _text(fill.get("code"))
            data = repository.read_document(code)
            existing = next(
                (item for item in data["lots"] if item.get("buy_fill_id") == _text(fill.get("fill_id"))),
                None,
            )
            if existing is not None:
                return {"success": True, "stage": "BUY_LOT_ALREADY_RECORDED", "changed": False, "lot": existing}
            delta = int(position_result.get("fill_delta_applied") or 0)
            if delta <= 0:
                return _failure("BUY_FILL_DELTA_MISSING", "positive committed BUY fill delta is required")
            episode, bootstrapped = _current_episode(root, fill)
            result = repository.record_buy_lot(fill, fill_delta=delta, entry_episode=episode)
            if not result.get("success"):
                return _failure(result.get("error_code", "BUY_LOT_FAILED"), result.get("error", "BUY entry-lot write failed"))
            return {
                "success": True,
                "stage": "BUY_ENTRY_LOT_RECORDED",
                "changed": result.get("changed") is True,
                "bootstrapped_episode": bootstrapped,
                "entry_episode_id": episode.episode_id,
                "lot": result.get("lot"),
            }
        except Exception as exc:
            return _failure("BUY_OWNERSHIP_MISMATCH", str(exc))


def prepare_sell_fifo_realization(
    project_root: Path | str,
    fill_record: dict[str, Any],
    position_result: dict[str, Any],
) -> dict[str, Any]:
    """Reserve deterministic FIFO allocations before realized P/L is written."""
    fill = dict(fill_record)
    if _text(fill.get("side")).upper() != "SELL":
        return _failure("NOT_SELL", "SELL FIFO linkage is not applicable")
    with _LINKAGE_LOCK:
        try:
            root = Path(project_root)
            delta = int(position_result.get("fill_delta_applied") or 0)
            lots_repository = CanonicalEntryLotRepository(root)
            if delta <= 0:
                document = lots_repository.read_document(_text(fill.get("code")))
                prior = next(
                    (
                        item
                        for field in ("pending_consumptions", "consumptions")
                        for item in document[field]
                        if item.get("fill_id") == _text(fill.get("fill_id"))
                    ),
                    None,
                )
                if prior is not None:
                    return {
                        "success": True,
                        "stage": "SELL_FIFO_ALREADY_RESERVED",
                        "changed": False,
                        "bootstrapped_episode": False,
                        "exit_episode_id": prior["exit_episode_id"],
                        "realization_id": prior["realization_id"],
                        "reservation": deepcopy(prior),
                        "allocations": deepcopy(prior["allocations"]),
                    }
            if delta <= 0:
                return _failure("SELL_FILL_DELTA_MISSING", "positive committed SELL fill delta is required")
            episode, bootstrapped = _current_episode(root, fill)
            realization_id = canonical_realization_id(fill, delta)
            result = lots_repository.reserve_fifo_consumption(
                _text(fill.get("code")),
                realization_id=realization_id,
                fill_id=_text(fill.get("fill_id")),
                quantity=delta,
                sell_price=fill.get("filled_price"),
                exit_episode_id=episode.episode_id,
            )
            if not result.get("success"):
                return _failure(result.get("error_code", "SELL_FIFO_FAILED"), result.get("error", "SELL FIFO reservation failed"))
            reservation = result["reservation"]
            return {
                "success": True,
                "stage": "SELL_FIFO_RESERVED",
                "changed": result.get("changed") is True,
                "bootstrapped_episode": bootstrapped,
                "exit_episode_id": episode.episode_id,
                "realization_id": realization_id,
                "reservation": deepcopy(reservation),
                "allocations": deepcopy(reservation["allocations"]),
            }
        except Exception as exc:
            return _failure("SELL_OWNERSHIP_MISMATCH", str(exc))


def append_performance_from_realization(
    project_root: Path | str,
    fill_record: dict[str, Any],
    realized_result: dict[str, Any],
    fifo_result: dict[str, Any],
) -> dict[str, Any]:
    """Append Performance Event, then durably commit its FIFO consumption."""
    if realized_result.get("realized_pnl_recorded") is not True:
        return _failure("REALIZED_PNL_NOT_DURABLE", "durable realized P/L evidence is required")
    if fifo_result.get("success") is not True:
        return _failure("FIFO_NOT_RESERVED", "durable FIFO reservation is required")
    fill = dict(fill_record)
    realization = realized_result.get("realization_record")
    if not isinstance(realization, dict):
        return _failure("REALIZATION_RECORD_MISSING", "realized P/L record is missing")
    with _LINKAGE_LOCK:
        try:
            root = Path(project_root)
            allocations = deepcopy(fifo_result["allocations"])
            event_net = realization.get("net_realized_profit")
            if len(allocations) == 1 and event_net is not None:
                allocations[0]["net_pnl"] = event_net
            broker = _text(fill.get("broker"))
            account = _text(fill.get("account_no"))
            execution_identity = _text(fill.get("execution_identity"))
            if not broker or not account or not execution_identity:
                return _failure(
                    "PERFORMANCE_IDENTITY_MISSING",
                    "broker, account, and broker execution identity are required",
                )
            event = {
                "stock_code": _text(fill.get("code")),
                "broker": broker,
                "account_number": account,
                "trade_date": realization.get("trade_date"),
                "broker_order_no": fill.get("broker_order_no"),
                "execution_identity": execution_identity,
                "fill_id": fill.get("fill_id"),
                "realization_id": realization.get("realization_id"),
                "realized_at": realization.get("realized_at"),
                "quantity": realization.get("sell_quantity"),
                "realized_cost_basis": realization.get("matched_cost_basis"),
                "gross_pnl": realization.get("gross_realized_profit"),
                "fee": realization.get("fee"),
                "tax": realization.get("tax"),
                "net_pnl": event_net,
                "exit_episode_id": fifo_result.get("exit_episode_id"),
                "canonical_owner_policy": CANONICAL_OWNER_POLICY,
                "allocations": allocations,
            }
            ledger = CanonicalStockPerformanceLedgerRepository(root)
            appended = ledger.append_event(event)
            if not appended.success:
                return _failure(appended.error_code or "PERFORMANCE_APPEND_FAILED", appended.error)
            committed = CanonicalEntryLotRepository(root).commit_fifo_consumption(
                _text(fill.get("code")),
                _text(realization.get("realization_id")),
            )
            if not committed.get("success"):
                return _failure(
                    committed.get("error_code", "FIFO_COMMIT_FAILED"),
                    committed.get("error", "Performance Event exists but FIFO consumption is pending"),
                    performance_event_id=(appended.event.performance_event_id if appended.event else ""),
                )
            return {
                "success": True,
                "stage": "PERFORMANCE_EVENT_RECORDED",
                "changed": appended.changed,
                "idempotent": appended.no_op,
                "performance_event": appended.event,
                "fifo_consumption": committed.get("consumption"),
            }
        except Exception as exc:
            return _failure("PERFORMANCE_LINKAGE_FAILED", str(exc))
