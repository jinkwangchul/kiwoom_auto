# -*- coding: utf-8 -*-
"""Canonical, append-only realized P/L ledger for confirmed SELL fill deltas."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "runtime" / "realized_pnl.json"
LEDGER_VERSION = 1
COST_BASIS_METHOD = "WEIGHTED_AVERAGE_POSITION"
_THREAD_LOCK = threading.RLock()
_LOCK_POLL_SECONDS = 0.02
_DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _json_number(value: float) -> int | float:
    return int(round(value)) if float(value).is_integer() else round(float(value), 6)


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "realized_pnl_recorded": False,
        "realized_pnl_stage": stage,
        "changed": False,
        "file_write": False,
        "post_write_verified": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    if ctx.get("manual_realized_pnl_confirmed") is True:
        return True
    return (
        ctx.get("kiwoom_api_live_event") is True
        and _clean(ctx.get("live_event_source")) == "KiwoomApi.raw_chejan_received"
    )


class _LedgerFileLock:
    def __init__(self, path: Path, timeout_sec: float) -> None:
        self.lock_path = path.with_name(f"{path.name}.lock")
        self.timeout_sec = timeout_sec
        self.handle: Any = None
        self.wait_ms = 0

    def __enter__(self) -> "_LedgerFileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.lock_path.open("a+b")
        started = time.monotonic()
        while True:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                self.wait_ms = int((time.monotonic() - started) * 1000)
                return self
            except OSError:
                if time.monotonic() - started >= self.timeout_sec:
                    self.wait_ms = int((time.monotonic() - started) * 1000)
                    self.handle.close()
                    self.handle = None
                    raise TimeoutError("realized P/L ledger lock timeout")
                time.sleep(_LOCK_POLL_SECONDS)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.handle.close()
            self.handle = None


def _read_data(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {"version": LEDGER_VERSION, "updated_at": None, "realizations": []}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"failed to read realized P/L ledger: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("realizations"), list):
        return {}, "realized P/L ledger schema is invalid"
    if any(not isinstance(item, dict) for item in data["realizations"]):
        return {}, "realized P/L ledger records must be objects"
    return data, ""


def read_realized_pnl_ledger(
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
) -> dict[str, Any]:
    path = Path(ledger_path)
    data, error = _read_data(path)
    if error:
        return {"ok": False, "records": (), "issues": (error,), "path": str(path)}
    return {
        "ok": True,
        "records": tuple(deepcopy(data["realizations"])),
        "issues": (),
        "path": str(path),
    }


def _trade_datetime(value: Any) -> tuple[str, str] | None:
    text = _clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.date().isoformat(), parsed.isoformat(timespec="seconds")


def _routine_instance_id(order_record: dict[str, Any]) -> str:
    provenance = _as_dict(order_record.get("routine_provenance"))
    return _clean(
        provenance.get("routine_instance_id")
        or order_record.get("routine_instance_id")
    )


def _realization_id(fill: dict[str, Any], fill_delta: int) -> str:
    identity = "|".join(
        (
            "REALIZED_PNL_V1",
            _clean(fill.get("fill_id")),
            _clean(fill.get("execution_identity")),
            str(fill_delta),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest().upper()


def _explicit_cost(fill: dict[str, Any], *keys: str) -> float | None:
    sources = (fill, _as_dict(fill.get("normalized_event")))
    for source in sources:
        for key in keys:
            if key not in source:
                continue
            value = _number(source.get(key))
            return value if value is not None and value >= 0 else None
    return None


def _existing_by_fill_id(records: list[dict[str, Any]], fill_id: str) -> dict[str, Any] | None:
    matches = [item for item in records if _clean(item.get("fill_id")) == fill_id]
    return matches[0] if len(matches) == 1 else None


def _daily_records(
    records: list[dict[str, Any]],
    *,
    trade_date: str,
    stock_code: str,
) -> list[dict[str, Any]]:
    return [
        item
        for item in records
        if _clean(item.get("trade_date")) == trade_date
        and _clean(item.get("stock_code")) == stock_code
    ]


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def record_realized_pnl(
    fill_record: Any,
    position_update_result: Any,
    order_record: Any,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    *,
    context: Any = None,
) -> dict[str, Any]:
    """Record one confirmed SELL fill delta; replay is idempotent by fill identity."""
    fill = _as_dict(fill_record)
    position = _as_dict(position_update_result)
    order = _as_dict(order_record)
    path = Path(ledger_path)
    if not _confirmed(context):
        return _blocked("confirmation", "realized P/L ledger confirmation is required")
    if _clean(fill.get("side")).upper() != "SELL":
        return {
            **_blocked("not_applicable", "fill side is not SELL"),
            "not_applicable": True,
        }
    fill_id = _clean(fill.get("fill_id"))
    if not fill_id:
        return _blocked("fill_identity", "fill_id is required")

    timeout = _number(_as_dict(context).get("realized_pnl_lock_timeout_sec"))
    timeout = _DEFAULT_LOCK_TIMEOUT_SECONDS if timeout is None or timeout < 0 else timeout
    try:
        with _THREAD_LOCK:
            with _LedgerFileLock(path, timeout) as lock:
                data, error = _read_data(path)
                if error:
                    return {**_blocked("read", error), "lock_acquired": True, "lock_wait_ms": lock.wait_ms}
                records = data["realizations"]
                existing = _existing_by_fill_id(records, fill_id)
                if existing is not None:
                    return {
                        "realized_pnl_recorded": True,
                        "realized_pnl_stage": "duplicate_realization",
                        "changed": False,
                        "idempotent": True,
                        "file_write": False,
                        "post_write_verified": True,
                        "realization_record": deepcopy(existing),
                        "lock_acquired": True,
                        "lock_wait_ms": lock.wait_ms,
                        "blocked_reasons": [],
                        "warnings": [],
                    }

                fill_delta_value = _number(position.get("fill_delta_applied"))
                previous_average = _number(position.get("previous_average_price"))
                sell_price = _number(fill.get("filled_price"))
                if position.get("position_updated") is not True:
                    return {**_blocked("position_evidence", "committed position update evidence is required"), "lock_acquired": True, "lock_wait_ms": lock.wait_ms}
                if fill_delta_value is None or fill_delta_value <= 0 or not fill_delta_value.is_integer():
                    return {**_blocked("fill_delta", "positive integer fill_delta_applied is required"), "lock_acquired": True, "lock_wait_ms": lock.wait_ms}
                if previous_average is None or previous_average <= 0:
                    return {**_blocked("cost_basis", "previous average price is unavailable"), "lock_acquired": True, "lock_wait_ms": lock.wait_ms}
                if sell_price is None or sell_price <= 0:
                    return {**_blocked("sell_price", "filled sell price is unavailable"), "lock_acquired": True, "lock_wait_ms": lock.wait_ms}
                occurred = _trade_datetime(fill.get("received_at") or fill.get("recorded_at"))
                if occurred is None:
                    return {**_blocked("realized_at", "valid fill timestamp is required"), "lock_acquired": True, "lock_wait_ms": lock.wait_ms}
                trade_date, realized_at = occurred
                stock_code = _clean(fill.get("code"))
                if not stock_code:
                    return {**_blocked("stock_code", "fill stock code is required"), "lock_acquired": True, "lock_wait_ms": lock.wait_ms}

                fill_delta = int(fill_delta_value)
                matched_cost = previous_average * fill_delta
                gross = sell_price * fill_delta - matched_cost
                fee = _explicit_cost(fill, "fee", "commission")
                tax = _explicit_cost(fill, "tax")
                costs_available = fee is not None and tax is not None
                net = gross - fee - tax if costs_available else None
                daily = _daily_records(records, trade_date=trade_date, stock_code=stock_code)
                cumulative_gross = sum(float(item["gross_realized_profit"]) for item in daily) + gross
                previous_nets = [item.get("net_realized_profit") for item in daily]
                cumulative_net = (
                    sum(float(value) for value in previous_nets) + net
                    if net is not None and all(value is not None for value in previous_nets)
                    else None
                )
                normalized = _as_dict(fill.get("normalized_event"))
                record = {
                    "realization_id": _realization_id(fill, fill_delta),
                    "trade_date": trade_date,
                    "stock_code": stock_code,
                    "stock_name": _clean(normalized.get("name")),
                    "routine_instance_id": _routine_instance_id(order),
                    "fill_id": fill_id,
                    "execution_identity_source": _clean(fill.get("execution_identity_source")),
                    "execution_identity": _clean(fill.get("execution_identity")),
                    "broker_order_no": _clean(fill.get("broker_order_no")),
                    "order_id": _clean(fill.get("order_id")),
                    "source_signal_id": _clean(order.get("source_signal_id")),
                    "realized_at": realized_at,
                    "sell_quantity": fill_delta,
                    "sell_price": _json_number(sell_price),
                    "matched_cost_basis": _json_number(matched_cost),
                    "gross_realized_profit": _json_number(gross),
                    "fee": _json_number(fee) if fee is not None else None,
                    "tax": _json_number(tax) if tax is not None else None,
                    "costs_available": costs_available,
                    "net_realized_profit": _json_number(net) if net is not None else None,
                    "cumulative_daily_gross_realized_profit": _json_number(cumulative_gross),
                    "cumulative_daily_realized_profit": _json_number(cumulative_net) if cumulative_net is not None else None,
                    "cost_basis_method": COST_BASIS_METHOD,
                    "source": "execution_fill+position_update",
                    "provenance": {
                        "fills_path": _clean(_as_dict(context).get("fills_path")),
                        "positions_path": _clean(position.get("positions_path")),
                        "position_id": _clean(position.get("position_id")),
                    },
                    "recorded_at": _now_text(),
                }
                updated = deepcopy(data)
                updated["version"] = LEDGER_VERSION
                updated["updated_at"] = record["recorded_at"]
                updated["realizations"] = list(records) + [record]
                _write_atomic(path, updated)
                verified, verify_error = _read_data(path)
                if verify_error or not any(item.get("realization_id") == record["realization_id"] for item in verified.get("realizations", [])):
                    return {**_blocked("post_write_verify", verify_error or "realization record not found after write"), "changed": True, "file_write": True, "lock_acquired": True, "lock_wait_ms": lock.wait_ms}
                return {
                    "realized_pnl_recorded": True,
                    "realized_pnl_stage": "realization_recorded",
                    "changed": True,
                    "idempotent": False,
                    "file_write": True,
                    "post_write_verified": True,
                    "ledger_path": str(path),
                    "realization_record": deepcopy(record),
                    "lock_acquired": True,
                    "lock_wait_ms": lock.wait_ms,
                    "blocked_reasons": [],
                    "warnings": [] if costs_available else ["fee/tax unavailable; net realized profit is unconfirmed"],
                }
    except TimeoutError:
        return _blocked("lock", "realized P/L ledger lock timeout")
    except Exception as exc:
        return _blocked("write", f"failed to record realized P/L: {exc}")


def project_daily_realized_pnl(
    stock_code: str,
    trade_date: str,
    *,
    routine_instance_id: str | None = None,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
) -> dict[str, Any]:
    result = read_realized_pnl_ledger(ledger_path)
    if result.get("ok") is not True:
        return {"available": False, "records": (), "issues": result.get("issues", ())}
    records = [
        item
        for item in result["records"]
        if _clean(item.get("stock_code")) == _clean(stock_code)
        and _clean(item.get("trade_date")) == _clean(trade_date)
        and (
            not _clean(routine_instance_id)
            or _clean(item.get("routine_instance_id")) == _clean(routine_instance_id)
        )
    ]
    records.sort(key=lambda item: (_clean(item.get("realized_at")), _clean(item.get("realization_id"))))
    if not records:
        return {
            "available": True,
            "records": (),
            "cumulative_daily_gross_realized_profit": 0,
            "cumulative_daily_realized_profit": 0,
            "net_available": True,
            "issues": (),
        }
    latest = records[-1]
    return {
        "available": True,
        "records": tuple(deepcopy(records)),
        "cumulative_daily_gross_realized_profit": latest.get("cumulative_daily_gross_realized_profit"),
        "cumulative_daily_realized_profit": latest.get("cumulative_daily_realized_profit"),
        "net_available": latest.get("cumulative_daily_realized_profit") is not None,
        "issues": (),
    }
