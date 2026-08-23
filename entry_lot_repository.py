"""Canonical FIFO entry-lot evidence derived from confirmed BUY fills."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import threading
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from assignment_episode_repository import AssignmentEpisode
from stock_repository import is_valid_stock_code, normalize_stock_code


ENTRY_LOT_SCHEMA_VERSION = "1.0"
PROJECT_ROOT = Path(__file__).resolve().parent
_WRITE_LOCK = threading.RLock()


def _text(value: object) -> str:
    return str(value or "").strip()


def _number(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError(f"{field} is required")
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _json_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else round(float(value), 6)


def _uuid(value: object, field: str) -> str:
    try:
        return str(UUID(_text(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _timestamp(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("created_at must include UTC offset")
    return parsed.isoformat(timespec="seconds")


class CanonicalEntryLotRepository:
    def __init__(
        self,
        project_root: Path | str = PROJECT_ROOT,
        *,
        ledger_root: Path | str | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.ledger_root = Path(ledger_root) if ledger_root else self.project_root / "performance_ledger"

    def document_path(self, stock_code: str) -> Path:
        code = normalize_stock_code(stock_code)
        if not is_valid_stock_code(code):
            raise ValueError("stock_code is invalid")
        return self.ledger_root / code / "entry_lots.json"

    def read_document(self, stock_code: str) -> dict[str, Any]:
        code = normalize_stock_code(stock_code)
        path = self.document_path(code)
        if not path.exists():
            return {
                "schema_version": ENTRY_LOT_SCHEMA_VERSION,
                "stock_code": code,
                "updated_at": None,
                "lots": [],
                "pending_consumptions": [],
                "consumptions": [],
            }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"failed to read canonical entry lots: {exc}") from exc
        self._validate_document(data, code)
        return data

    def record_buy_lot(
        self,
        fill: dict[str, Any],
        *,
        fill_delta: int,
        entry_episode: AssignmentEpisode,
    ) -> dict[str, Any]:
        code = normalize_stock_code(_text(fill.get("code")))
        fill_id = _text(fill.get("fill_id"))
        execution_identity = _text(fill.get("execution_identity"))
        price = _number(fill.get("filled_price"), "filled_price")
        if _text(fill.get("side")).upper() != "BUY":
            return self._failure("NOT_BUY", "entry lot requires a BUY fill")
        if not fill_id or not execution_identity or fill_delta <= 0 or price <= 0:
            return self._failure("BUY_LOT_EVIDENCE_INVALID", "BUY fill identity, delta, and price are required")
        if entry_episode.stock_code != code or not entry_episode.is_open:
            return self._failure("ENTRY_EPISODE_INVALID", "BUY requires the matching open assignment Episode")
        lot_id = str(uuid5(NAMESPACE_URL, "|".join(("ENTRY_LOT_V1", code, fill_id, execution_identity))))
        with _WRITE_LOCK:
            try:
                data = self.read_document(code)
                existing = next((item for item in data["lots"] if item["entry_lot_id"] == lot_id), None)
                if existing is not None:
                    return {"success": True, "changed": False, "no_op": True, "lot": deepcopy(existing)}
                if any(item.get("buy_fill_id") == fill_id for item in data["lots"]):
                    return self._failure("BUY_LOT_CONFLICT", "fill_id already owns a different entry lot")
                created_at = _timestamp(fill.get("received_at") or fill.get("recorded_at"))
                lot = {
                    "entry_lot_id": lot_id,
                    "stock_code": code,
                    "buy_fill_id": fill_id,
                    "buy_execution_identity": execution_identity,
                    "quantity": int(fill_delta),
                    "remaining_quantity": int(fill_delta),
                    "unit_price": _json_number(price),
                    "cost_basis": _json_number(price * fill_delta),
                    "entry_episode_id": entry_episode.episode_id,
                    "created_at": created_at,
                }
                updated = deepcopy(data)
                updated["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                updated["lots"].append(lot)
                self._write_document(code, updated)
                return {"success": True, "changed": True, "no_op": False, "lot": deepcopy(lot)}
            except Exception as exc:
                return self._failure("BUY_LOT_WRITE_FAILED", str(exc))

    def reserve_fifo_consumption(
        self,
        stock_code: str,
        *,
        realization_id: str,
        fill_id: str,
        quantity: int,
        sell_price: int | float,
        exit_episode_id: str,
    ) -> dict[str, Any]:
        code = normalize_stock_code(stock_code)
        clean_realization = _text(realization_id)
        price = _number(sell_price, "sell_price")
        clean_exit_episode_id = _uuid(exit_episode_id, "exit_episode_id")
        if not clean_realization or not _text(fill_id) or quantity <= 0 or price <= 0:
            return self._failure("FIFO_EVIDENCE_INVALID", "realization, fill, quantity, and price are required")
        with _WRITE_LOCK:
            try:
                data = self.read_document(code)
                consumed = next((item for item in data["consumptions"] if item["realization_id"] == clean_realization), None)
                if consumed is not None:
                    return {"success": True, "changed": False, "no_op": True, "committed": True, "reservation": deepcopy(consumed)}
                pending = next((item for item in data["pending_consumptions"] if item["realization_id"] == clean_realization), None)
                if pending is not None:
                    return {"success": True, "changed": False, "no_op": True, "committed": False, "reservation": deepcopy(pending)}
                if data["pending_consumptions"]:
                    return self._failure("FIFO_PENDING_EXISTS", "another SELL consumption is pending canonical completion")
                remaining = quantity
                allocations: list[dict[str, Any]] = []
                for lot in sorted(data["lots"], key=lambda item: (item["created_at"], item["entry_lot_id"])):
                    available = int(lot["remaining_quantity"])
                    if available <= 0:
                        continue
                    used = min(available, remaining)
                    unit_price = _number(lot["unit_price"], "lot.unit_price")
                    cost = unit_price * used
                    allocations.append(
                        {
                            "entry_lot_id": lot["entry_lot_id"],
                            "entry_episode_id": lot["entry_episode_id"],
                            "quantity": used,
                            "cost_basis": _json_number(cost),
                            "gross_pnl": _json_number(price * used - cost),
                            "net_pnl": None,
                        }
                    )
                    remaining -= used
                    if remaining == 0:
                        break
                if remaining:
                    return self._failure("FIFO_LOTS_INSUFFICIENT", "canonical BUY entry lots do not cover SELL quantity")
                reservation = {
                    "realization_id": clean_realization,
                    "fill_id": _text(fill_id),
                    "quantity": quantity,
                    "sell_price": _json_number(price),
                    "exit_episode_id": clean_exit_episode_id,
                    "allocations": allocations,
                    "reserved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
                updated = deepcopy(data)
                updated["updated_at"] = reservation["reserved_at"]
                updated["pending_consumptions"].append(reservation)
                self._write_document(code, updated)
                return {"success": True, "changed": True, "no_op": False, "committed": False, "reservation": deepcopy(reservation)}
            except Exception as exc:
                return self._failure("FIFO_RESERVATION_FAILED", str(exc))

    def commit_fifo_consumption(self, stock_code: str, realization_id: str) -> dict[str, Any]:
        code = normalize_stock_code(stock_code)
        clean_realization = _text(realization_id)
        with _WRITE_LOCK:
            try:
                data = self.read_document(code)
                consumed = next((item for item in data["consumptions"] if item["realization_id"] == clean_realization), None)
                if consumed is not None:
                    return {"success": True, "changed": False, "no_op": True, "consumption": deepcopy(consumed)}
                pending = next((item for item in data["pending_consumptions"] if item["realization_id"] == clean_realization), None)
                if pending is None:
                    return self._failure("FIFO_RESERVATION_MISSING", "SELL FIFO reservation does not exist")
                updated = deepcopy(data)
                lots_by_id = {item["entry_lot_id"]: item for item in updated["lots"]}
                for allocation in pending["allocations"]:
                    lot = lots_by_id.get(allocation["entry_lot_id"])
                    quantity = int(allocation["quantity"])
                    if lot is None or int(lot["remaining_quantity"]) < quantity:
                        return self._failure("FIFO_COMMIT_CONFLICT", "entry lot remainder changed before commit")
                    lot["remaining_quantity"] = int(lot["remaining_quantity"]) - quantity
                committed = deepcopy(pending)
                committed["committed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                updated["pending_consumptions"] = [
                    item for item in updated["pending_consumptions"] if item["realization_id"] != clean_realization
                ]
                updated["consumptions"].append(committed)
                updated["updated_at"] = committed["committed_at"]
                self._write_document(code, updated)
                return {"success": True, "changed": True, "no_op": False, "consumption": committed}
            except Exception as exc:
                return self._failure("FIFO_COMMIT_FAILED", str(exc))

    @staticmethod
    def _failure(code: str, error: str) -> dict[str, Any]:
        return {"success": False, "changed": False, "no_op": False, "error_code": code, "error": error}

    def _write_document(self, stock_code: str, data: dict[str, Any]) -> None:
        self._validate_document(data, stock_code)
        path = self.document_path(stock_code)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._validate_document(json.loads(path.read_text(encoding="utf-8")), stock_code)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_document(data: object, stock_code: str) -> None:
        if not isinstance(data, dict) or data.get("schema_version") != ENTRY_LOT_SCHEMA_VERSION:
            raise ValueError("entry lot document schema is invalid")
        if data.get("stock_code") != stock_code:
            raise ValueError("entry lot stock_code mismatch")
        for field in ("lots", "pending_consumptions", "consumptions"):
            if not isinstance(data.get(field), list) or any(not isinstance(item, dict) for item in data[field]):
                raise ValueError(f"entry lot {field} must be a list of objects")
        seen: set[str] = set()
        for lot in data["lots"]:
            lot_id = _uuid(lot.get("entry_lot_id"), "entry_lot_id")
            if lot_id in seen:
                raise ValueError("duplicate entry_lot_id")
            seen.add(lot_id)
            _uuid(lot.get("entry_episode_id"), "entry_episode_id")
            quantity = int(lot.get("quantity", 0))
            remaining = int(lot.get("remaining_quantity", -1))
            if quantity <= 0 or remaining < 0 or remaining > quantity:
                raise ValueError("entry lot quantity is invalid")
            if _number(lot.get("unit_price"), "unit_price") <= 0:
                raise ValueError("entry lot unit_price is invalid")
            _timestamp(lot.get("created_at"))
