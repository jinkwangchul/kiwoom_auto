# -*- coding: utf-8 -*-
"""Append-only observer for raw Kiwoom Chejan trade-cost fields."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DIAGNOSTIC_PATH = (
    PROJECT_ROOT / "runtime" / "diagnostics" / "kiwoom_trade_cost_chejan" / "events.jsonl"
)
DEFAULT_ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"

SCHEMA = "kiwoom_trade_cost_chejan_diagnostic_v1"
_WRITE_LOCK = threading.Lock()

RAW_FIELD_FIDS = {
    "account_no": "9201",
    "stock_code": "9001",
    "stock_name": "302",
    "broker_order_no": "9203",
    "original_order_no": "904",
    "side": "907",
    "order_quantity": "900",
    "order_price": "901",
    "filled_price": "910",
    "filled_quantity": "911",
    "remaining_quantity": "902",
    "cumulative_fill_amount": "903",
    "order_status": "913",
    "chejan_time": "908",
    "raw_938": "938",
    "raw_939": "939",
}


def _raw_text(fid_raw_values: dict[str, Any], fid: str) -> str:
    value = fid_raw_values.get(fid)
    if value is None:
        return ""
    return str(value)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _read_orders(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    orders = payload.get("orders") if isinstance(payload, dict) else None
    if not isinstance(orders, list):
        return []
    return [item for item in orders if isinstance(item, dict)]


def _internal_identity(raw_fields: dict[str, str], queue_path: Path) -> dict[str, Any]:
    orders = _read_orders(queue_path)
    broker_order_no = raw_fields["broker_order_no"].strip()
    original_order_no = raw_fields["original_order_no"].strip()

    exact = [
        item
        for item in orders
        if broker_order_no
        and broker_order_no
        in {
            _clean(item.get("broker_order_no")),
            _clean(item.get("latest_modify_broker_order_no")),
        }
    ]
    matched_by = "broker_order_no"
    candidates = exact

    if len(candidates) != 1 and original_order_no:
        candidates = [
            item
            for item in orders
            if original_order_no
            in {
                _clean(item.get("broker_order_no")),
                _clean(item.get("original_order_no")),
            }
        ]
        matched_by = "original_order_no"

    if len(candidates) != 1:
        return {
            "matched": False,
            "matched_by": "ambiguous" if candidates else "not_found",
            "candidate_count": len(candidates),
            "order_queued_id": "",
            "order_id": "",
            "execution_id": "",
            "broker_order_no": "",
        }

    item = candidates[0]
    return {
        "matched": True,
        "matched_by": matched_by,
        "candidate_count": 1,
        "order_queued_id": _clean(item.get("order_queued_id") or item.get("id")),
        "order_id": _clean(item.get("order_id")),
        "execution_id": _clean(item.get("execution_id")),
        "broker_order_no": _clean(item.get("broker_order_no")),
    }


def build_trade_cost_chejan_diagnostic(
    raw_event: dict[str, Any],
    *,
    order_queue_path: Path = DEFAULT_ORDER_QUEUE_PATH,
) -> dict[str, Any]:
    """Build one observation record without changing Production state."""

    fid_raw_values = raw_event.get("fid_raw_values")
    if not isinstance(fid_raw_values, dict):
        fallback = raw_event.get("fid_values")
        fid_raw_values = fallback if isinstance(fallback, dict) else {}
    raw_fields = {
        name: _raw_text(fid_raw_values, fid)
        for name, fid in RAW_FIELD_FIDS.items()
    }
    return {
        "schema": SCHEMA,
        "diagnostic_recorded_at": datetime.now().isoformat(timespec="milliseconds"),
        "event_received_at": _clean(raw_event.get("received_at")),
        "server_raw": {
            "source": _clean(raw_event.get("source")),
            "gubun": _clean(raw_event.get("gubun")),
            "item_count": raw_event.get("item_count"),
            "fid_list": deepcopy(raw_event.get("fid_list") or []),
            "observed_fid_list": deepcopy(raw_event.get("observed_fid_list") or []),
            "fields": raw_fields,
            "fid_raw_values": deepcopy(fid_raw_values),
        },
        "internal_identity": _internal_identity(raw_fields, order_queue_path),
    }


def record_trade_cost_chejan_diagnostic(
    raw_event: dict[str, Any],
    *,
    diagnostic_path: Path = DEFAULT_DIAGNOSTIC_PATH,
    order_queue_path: Path = DEFAULT_ORDER_QUEUE_PATH,
) -> dict[str, Any]:
    """Append one raw observation. Failures are isolated from Chejan handling."""

    try:
        record = build_trade_cost_chejan_diagnostic(
            raw_event,
            order_queue_path=order_queue_path,
        )
        diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _WRITE_LOCK:
            with diagnostic_path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()
        return {
            "recorded": True,
            "diagnostic_path": str(diagnostic_path),
            "record": record,
        }
    except Exception as exc:
        return {
            "recorded": False,
            "diagnostic_path": str(diagnostic_path),
            "error": str(exc),
        }


def read_trade_cost_chejan_diagnostics(
    diagnostic_path: Path = DEFAULT_DIAGNOSTIC_PATH,
) -> list[dict[str, Any]]:
    try:
        lines = diagnostic_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def format_trade_cost_chejan_table(records: list[dict[str, Any]]) -> str:
    """Return a compact chronological view; JSONL retains the full snapshot."""

    headers = ("time", "stock", "side", "order_no", "fill_qty", "cum_amount", "938", "939")
    rows = ["\t".join(headers)]
    for record in records:
        server = record.get("server_raw") if isinstance(record, dict) else None
        fields = server.get("fields") if isinstance(server, dict) else None
        if not isinstance(fields, dict):
            fields = {}
        rows.append(
            "\t".join(
                [
                    _clean(record.get("event_received_at")),
                    _clean(fields.get("stock_code")),
                    _clean(fields.get("side")),
                    _clean(fields.get("broker_order_no")),
                    _clean(fields.get("filled_quantity")),
                    _clean(fields.get("cumulative_fill_amount")),
                    _clean(fields.get("raw_938")),
                    _clean(fields.get("raw_939")),
                ]
            )
        )
    return "\n".join(rows)


if __name__ == "__main__":
    print(format_trade_cost_chejan_table(read_trade_cost_chejan_diagnostics()))
