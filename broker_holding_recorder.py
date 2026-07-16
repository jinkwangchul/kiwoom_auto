# -*- coding: utf-8 -*-
"""Record Kiwoom broker holding snapshots from live Chejan balance events.

This module owns only the explicit broker_holdings_path. It does not update
positions, fills, order queues, broker APIs, GUI state, or cash balances.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any
from uuid import uuid4


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_HOLDING_RECORDED = "BROKER_HOLDING_RECORDED"
_BROKER_HOLDING_THREAD_LOCK = threading.RLock()
_LOCK_POLL_SECONDS = 0.02
_DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
_MAX_EVENT_IDENTITIES_PER_HOLDING = 20


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_received_at(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    parsed: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            pass
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _received_at_has_timezone(value: Any) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _decimal_from_text(value: Any) -> Decimal | None:
    text = _clean_text(value).replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return number


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "holding_recorded": False,
        "holding_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": False,
        "file_write": False,
        "holding_write": False,
        "holding_committed": False,
        "post_write_verified": False,
        "lock_acquired": False,
        "lock_wait_ms": 0,
        "manual_reconciliation_required": True,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _noop(stage: str, reason: str) -> dict[str, Any]:
    return {
        "holding_recorded": False,
        "holding_stage": stage,
        "next_stage": NEXT_STAGE_HOLDING_RECORDED,
        "changed": False,
        "file_write": False,
        "holding_write": False,
        "holding_committed": False,
        "post_write_verified": True,
        "lock_acquired": True,
        "lock_wait_ms": 0,
        "manual_reconciliation_required": False,
        "blocked_reasons": [],
        "warnings": [reason],
    }


def _post_write_failed(stage: str, reason: str) -> dict[str, Any]:
    result = _blocked(stage, reason)
    result.update(
        {
            "changed": True,
            "file_write": True,
            "holding_write": True,
            "holding_committed": True,
            "post_write_verified": False,
        }
    )
    return result


def _with_lock_metadata(result: dict[str, Any], *, lock_acquired: bool, lock_wait_ms: int = 0) -> dict[str, Any]:
    updated = deepcopy(result)
    updated["lock_acquired"] = lock_acquired
    updated["lock_wait_ms"] = lock_wait_ms
    updated.setdefault("file_write", False)
    updated.setdefault("holding_write", False)
    updated.setdefault("holding_committed", False)
    updated.setdefault("post_write_verified", False)
    return updated


def _confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return (
        ctx.get("kiwoom_api_live_event") is True
        and _clean_text(ctx.get("live_event_source")) == "KiwoomApi.raw_chejan_received"
    )


def _lock_timeout_sec(context: Any) -> float:
    value = _as_dict(context).get("broker_holding_lock_timeout_sec")
    if isinstance(value, bool):
        return _DEFAULT_LOCK_TIMEOUT_SECONDS
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return _DEFAULT_LOCK_TIMEOUT_SECONDS


class _BrokerHoldingFileLock:
    def __init__(self, target_path: Path, timeout_sec: float) -> None:
        self.lock_path = target_path.with_name(f"{target_path.name}.lock")
        self.timeout_sec = timeout_sec
        self.handle: Any = None
        self.wait_ms = 0

    def __enter__(self) -> "_BrokerHoldingFileLock":
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
                    raise TimeoutError("broker holdings lock timeout")
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


def _write_json_temp(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return tmp_path


def _cleanup_temp(path: Path | None) -> None:
    if path is not None and path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _parse_int(value: Any, field: str, errors: list[str]) -> int | None:
    number = _decimal_from_text(value)
    if number is None:
        errors.append(f"{field} is required")
        return None
    if number != number.to_integral_value():
        errors.append(f"{field} must be an integer")
        return None
    if number < 0:
        errors.append(f"{field} must not be negative")
        return None
    return int(number)


def _parse_optional_number(value: Any, field: str, warnings: list[str]) -> int | float | None:
    if not _clean_text(value):
        warnings.append(f"{field} is missing")
        return None
    number = _decimal_from_text(value)
    if number is None:
        warnings.append(f"{field} is not finite numeric")
        return None
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def _parse_required_number(value: Any, field: str, errors: list[str]) -> int | float | None:
    number = _decimal_from_text(value)
    if number is None:
        errors.append(f"{field} is required")
        return None
    if number < 0:
        errors.append(f"{field} must not be negative")
        return None
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def _decimal_value(value: Any, field: str) -> tuple[Decimal | None, str]:
    if not _clean_text(value):
        return None, f"{field} is missing"
    number = _decimal_from_text(value)
    if number is None:
        return None, f"{field} must be finite numeric"
    if number < 0:
        return None, f"{field} must not be negative"
    return number, ""


def _code_from_fid(value: Any) -> str:
    text = _clean_text(value).upper()
    return text[1:] if text.startswith("A") else text


def normalize_broker_holding_chejan_event(raw_event: Any, context: Any = None) -> dict[str, Any]:
    event = _as_dict(raw_event)
    if _clean_text(event.get("gubun")) != "1":
        return _blocked("normalize_broker_holding", "Chejan gubun is not broker holding balance")
    if not _confirmed(context):
        return _blocked("broker_holding_source", "live KiwoomApi raw Chejan context is required")

    fids = _as_dict(event.get("fid_values"))
    errors: list[str] = []
    warnings: list[str] = []
    account_no = _clean_text(fids.get("9201"))
    code = _code_from_fid(fids.get("9001"))
    name = _clean_text(fids.get("302"))
    if not account_no:
        errors.append("account_no is required")
    if not code:
        errors.append("code is required")
    holding_quantity = _parse_int(fids.get("930"), "holding_quantity", errors)
    available_quantity = _parse_int(fids.get("933"), "available_quantity", errors)
    average_price = _parse_required_number(fids.get("931"), "average_price", errors)
    total_purchase_amount = _parse_optional_number(fids.get("932"), "total_purchase_amount", warnings)
    current_price = _parse_optional_number(fids.get("10"), "current_price", warnings)
    if isinstance(current_price, (int, float)):
        current_price = abs(current_price)
    profit_loss_rate = _parse_optional_number(fids.get("8019"), "profit_loss_rate", warnings)
    received_at = _clean_text(event.get("received_at")) or _now_text()
    if _parse_received_at(received_at) is None:
        errors.append("received_at must be comparable")

    if errors:
        blocked = _blocked("normalize_broker_holding", "; ".join(errors))
        blocked["warnings"] = warnings
        blocked["raw_event"] = deepcopy(event)
        return blocked

    identity_payload = {
        "account_no": account_no,
        "code": code,
        "received_at": received_at,
        "fid_values": {str(key): _clean_text(value) for key, value in sorted(fids.items())},
    }
    encoded = json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    event_identity = hashlib.sha256(encoded).hexdigest().upper()
    return {
        "holding_normalized": True,
        "holding_stage": "broker_holding_normalized",
        "account_no": account_no,
        "code": code,
        "name": name,
        "holding_quantity": holding_quantity,
        "available_quantity": available_quantity,
        "average_price": average_price,
        "total_purchase_amount": total_purchase_amount,
        "current_price": current_price,
        "profit_loss_rate": profit_loss_rate,
        "received_at": received_at,
        "event_identity": event_identity,
        "event_identity_source": "kiwoom_balance_fid_snapshot",
        "raw_fid_values": deepcopy(fids),
        "warnings": warnings,
        "blocked_reasons": [],
    }


def _read_holdings(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not path.exists():
        return {"version": 1, "updated_at": None, "holdings": []}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, _blocked("read_broker_holdings", f"failed to read broker holdings json: {exc}")
    if not isinstance(data, dict):
        return {}, _blocked("read_broker_holdings", "broker holdings root must be an object")
    holdings = data.get("holdings")
    if not isinstance(holdings, list):
        return {}, _blocked("read_broker_holdings", "holdings must be a list")
    for item in holdings:
        if not isinstance(item, dict):
            return {}, _blocked("read_broker_holdings", "holdings must contain only objects")
    return data, None


def _read_positions_for_compare(path: Path) -> tuple[list[dict[str, Any]], str]:
    if not path.exists():
        return [], ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"failed to read positions json: {exc}"
    if not isinstance(data, dict):
        return [], "positions root must be an object"
    positions = data.get("positions")
    if not isinstance(positions, list):
        return [], "positions must be a list"
    records: list[dict[str, Any]] = []
    for item in positions:
        if not isinstance(item, dict):
            return [], "positions must contain only objects"
        records.append(dict(item))
    return records, ""


def _find_internal_position(positions: list[dict[str, Any]], account_no: str, code: str) -> tuple[dict[str, Any] | None, str]:
    matches = [
        item for item in positions
        if _clean_text(item.get("account_no")) == account_no and _clean_text(item.get("code")) == code
    ]
    if len(matches) > 1:
        return None, "multiple internal positions found for account_no + code"
    if len(matches) != 1:
        return None, ""
    return matches[0], ""


def _reconciliation_status(snapshot: dict[str, Any], positions_path: Path) -> dict[str, Any]:
    positions, read_failure = _read_positions_for_compare(positions_path)
    detected_at = _now_text()
    broker_qty = snapshot["holding_quantity"]
    broker_avg = Decimal(str(snapshot["average_price"]))
    if read_failure:
        return {
            "status": "POSITION_SOURCE_INVALID",
            "manual_reconciliation_required": True,
            "mismatch_fields": ["position_source"],
            "internal_quantity": None,
            "internal_average_price": None,
            "broker_holding_quantity": broker_qty,
            "broker_average_price": snapshot["average_price"],
            "position_read_failure_reason": read_failure,
            "detected_at": detected_at,
        }

    position, position_error = _find_internal_position(positions, snapshot["account_no"], snapshot["code"])
    if position_error:
        return {
            "status": "POSITION_SOURCE_INVALID",
            "manual_reconciliation_required": True,
            "mismatch_fields": ["position_source"],
            "internal_quantity": None,
            "internal_average_price": None,
            "broker_holding_quantity": broker_qty,
            "broker_average_price": snapshot["average_price"],
            "position_read_failure_reason": position_error,
            "detected_at": detected_at,
        }
    if position is None:
        status = "BROKER_ONLY" if broker_qty > 0 else "CONSISTENT"
        return {
            "status": status,
            "manual_reconciliation_required": status != "CONSISTENT",
            "mismatch_fields": [] if status == "CONSISTENT" else ["position_missing"],
            "internal_quantity": None,
            "internal_average_price": None,
            "broker_holding_quantity": broker_qty,
            "broker_average_price": snapshot["average_price"],
            "position_read_failure_reason": "",
            "detected_at": detected_at,
        }

    internal_qty_decimal, qty_error = _decimal_value(position.get("quantity"), "position.quantity")
    internal_avg, avg_error = _decimal_value(position.get("average_price"), "position.average_price")
    if qty_error or avg_error or internal_qty_decimal != internal_qty_decimal.to_integral_value():
        reason = "; ".join(reason for reason in (qty_error, avg_error, "position.quantity must be an integer" if internal_qty_decimal is not None and internal_qty_decimal != internal_qty_decimal.to_integral_value() else "") if reason)
        return {
            "status": "POSITION_SOURCE_INVALID",
            "manual_reconciliation_required": True,
            "mismatch_fields": ["position_source"],
            "internal_quantity": None,
            "internal_average_price": None,
            "broker_holding_quantity": broker_qty,
            "broker_average_price": snapshot["average_price"],
            "position_read_failure_reason": reason,
            "detected_at": detected_at,
        }
    internal_qty = int(internal_qty_decimal)
    mismatch_fields: list[str] = []
    if broker_qty == 0 and internal_qty > 0:
        status = "INTERNAL_ONLY"
        mismatch_fields.append("holding_quantity")
    elif internal_qty != broker_qty:
        status = "QUANTITY_MISMATCH"
        mismatch_fields.append("holding_quantity")
    elif internal_avg != broker_avg:
        status = "AVERAGE_PRICE_MISMATCH"
        mismatch_fields.append("average_price")
    else:
        status = "CONSISTENT"

    return {
        "status": status,
        "manual_reconciliation_required": status != "CONSISTENT",
        "mismatch_fields": mismatch_fields,
        "internal_quantity": internal_qty,
        "internal_average_price": int(internal_avg) if internal_avg == internal_avg.to_integral_value() else float(internal_avg),
        "broker_holding_quantity": broker_qty,
        "broker_average_price": snapshot["average_price"],
        "position_read_failure_reason": "",
        "detected_at": detected_at,
    }


def _holding_record(snapshot: dict[str, Any], reconciliation: dict[str, Any], now: str) -> dict[str, Any]:
    return {
        "account_no": snapshot["account_no"],
        "code": snapshot["code"],
        "name": snapshot.get("name", ""),
        "holding_quantity": snapshot["holding_quantity"],
        "available_quantity": snapshot["available_quantity"],
        "average_price": snapshot["average_price"],
        "total_purchase_amount": snapshot.get("total_purchase_amount"),
        "current_price": snapshot.get("current_price"),
        "profit_loss_rate": snapshot.get("profit_loss_rate"),
        "received_at": snapshot["received_at"],
        "recorded_at": now,
        "event_identity": snapshot["event_identity"],
        "event_identity_source": snapshot["event_identity_source"],
        "event_identities": [snapshot["event_identity"]],
        "raw_fid_values": deepcopy(snapshot.get("raw_fid_values", {})),
        "reconciliation_status": reconciliation["status"],
        "manual_reconciliation_required": reconciliation["manual_reconciliation_required"],
        "mismatch_fields": list(reconciliation["mismatch_fields"]),
        "internal_quantity": reconciliation["internal_quantity"],
        "internal_average_price": reconciliation["internal_average_price"],
        "broker_holding_quantity": reconciliation["broker_holding_quantity"],
        "broker_average_price": reconciliation["broker_average_price"],
        "position_read_failure_reason": reconciliation["position_read_failure_reason"],
        "reconciliation_detected_at": reconciliation["detected_at"],
    }


def record_broker_holding_snapshot(
    raw_event: Any,
    broker_holdings_path: str | Path,
    positions_path: str | Path,
    context: Any = None,
    backup: bool = True,
) -> dict[str, Any]:
    snapshot = normalize_broker_holding_chejan_event(raw_event, context=context)
    if snapshot.get("holding_normalized") is not True:
        return snapshot

    target_path = Path(broker_holdings_path)
    positions_target = Path(positions_path)
    try:
        with _BROKER_HOLDING_THREAD_LOCK:
            with _BrokerHoldingFileLock(target_path, _lock_timeout_sec(context)) as lock:
                before_sha256 = _sha256_file(target_path) if target_path.exists() else None
                data, read_blocked = _read_holdings(target_path)
                if read_blocked is not None:
                    return _with_lock_metadata(read_blocked, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                holdings = data["holdings"]
                existing_index = -1
                matching_indexes: list[int] = []
                for index, item in enumerate(holdings):
                    if (
                        _clean_text(item.get("account_no")) == snapshot["account_no"]
                        and _clean_text(item.get("code")) == snapshot["code"]
                    ):
                        matching_indexes.append(index)
                if len(matching_indexes) > 1:
                    return _with_lock_metadata(
                        _blocked("broker_holdings_source_integrity", "duplicate broker holding records for account_no + code"),
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )
                if matching_indexes:
                    existing_index = matching_indexes[0]
                    item = _as_dict(holdings[existing_index])
                    existing_identities = item.get("event_identities")
                    if isinstance(existing_identities, list) and snapshot["event_identity"] in existing_identities:
                        result = _noop("duplicate_broker_holding_event", "broker holding event already recorded")
                        result.update(
                            {
                                "account_no": snapshot["account_no"],
                                "code": snapshot["code"],
                                "event_identity": snapshot["event_identity"],
                                "reconciliation_status": _clean_text(item.get("reconciliation_status")),
                                "manual_reconciliation_required": item.get("manual_reconciliation_required") is True,
                                "mismatch_fields": list(item.get("mismatch_fields") or []) if isinstance(item.get("mismatch_fields"), list) else [],
                                "before_sha256": before_sha256,
                            }
                        )
                        return _with_lock_metadata(result, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                if existing_index >= 0:
                    existing = _as_dict(holdings[existing_index])
                    existing_received_at = _parse_received_at(existing.get("received_at"))
                    incoming_received_at = _parse_received_at(snapshot.get("received_at"))
                    if existing_received_at is None or incoming_received_at is None:
                        return _with_lock_metadata(
                            _blocked("broker_holding_received_at", "existing or incoming received_at is not comparable"),
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )
                    if _received_at_has_timezone(existing.get("received_at")) != _received_at_has_timezone(snapshot.get("received_at")):
                        return _with_lock_metadata(
                            _blocked("broker_holding_received_at", "mixed timezone-aware and timezone-naive received_at cannot be compared"),
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )
                    if incoming_received_at < existing_received_at:
                        result = _noop("stale_broker_holding_event", "older broker holding event did not replace latest snapshot")
                        result.update(
                            {
                                "account_no": snapshot["account_no"],
                                "code": snapshot["code"],
                                "event_identity": snapshot["event_identity"],
                                "reconciliation_status": _clean_text(existing.get("reconciliation_status")),
                                "manual_reconciliation_required": existing.get("manual_reconciliation_required") is True,
                                "mismatch_fields": list(existing.get("mismatch_fields") or []) if isinstance(existing.get("mismatch_fields"), list) else [],
                                "before_sha256": before_sha256,
                            }
                        )
                        return _with_lock_metadata(result, lock_acquired=True, lock_wait_ms=lock.wait_ms)
                    if incoming_received_at == existing_received_at:
                        return _with_lock_metadata(
                            _blocked("ambiguous_broker_holding_event", "same received_at with a different event identity cannot replace latest snapshot"),
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )

                reconciliation = _reconciliation_status(snapshot, positions_target)
                now = _now_text()
                record = _holding_record(snapshot, reconciliation, now)
                if existing_index >= 0:
                    existing = dict(holdings[existing_index])
                    identities = existing.get("event_identities")
                    prior = list(identities) if isinstance(identities, list) else []
                    record["event_identities"] = (prior + [snapshot["event_identity"]])[-_MAX_EVENT_IDENTITIES_PER_HOLDING:]
                updated = deepcopy(data)
                updated["version"] = updated.get("version", 1)
                updated["updated_at"] = now
                if existing_index >= 0:
                    updated["holdings"][existing_index] = record
                else:
                    updated["holdings"].append(record)

                backup_path = None
                if backup and target_path.exists():
                    backup_path = str(target_path) + ".bak"
                    try:
                        shutil.copy2(target_path, backup_path)
                    except Exception as exc:
                        return _with_lock_metadata(
                            _blocked("backup", f"failed to create backup: {exc}"),
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )

                tmp_path = None
                try:
                    tmp_path = _write_json_temp(target_path, updated)
                    os.replace(tmp_path, target_path)
                    tmp_path = None
                except Exception as exc:
                    _cleanup_temp(tmp_path)
                    return _with_lock_metadata(
                        _blocked("write_broker_holdings", f"failed to write broker holdings json: {exc}"),
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                after_sha256 = _sha256_file(target_path)
                post_data, post_blocked = _read_holdings(target_path)
                if post_blocked is not None:
                    failed = _post_write_failed("post_write_verify", post_blocked["blocked_reasons"][0])
                    failed.update({"before_sha256": before_sha256, "after_sha256": after_sha256, "backup_path": backup_path})
                    return _with_lock_metadata(failed, lock_acquired=True, lock_wait_ms=lock.wait_ms)
                if post_data != updated:
                    failed = _post_write_failed("post_write_verify", "broker holdings json did not match expected data after write")
                    failed.update({"before_sha256": before_sha256, "after_sha256": after_sha256, "backup_path": backup_path})
                    return _with_lock_metadata(failed, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                return _with_lock_metadata(
                    {
                        "holding_recorded": True,
                        "holding_stage": "broker_holding_recorded",
                        "next_stage": NEXT_STAGE_HOLDING_RECORDED,
                        "changed": True,
                        "file_write": True,
                        "holding_write": True,
                        "holding_committed": True,
                        "post_write_verified": True,
                        "broker_holdings_path": str(target_path),
                        "positions_path": str(positions_target),
                        "backup_path": backup_path,
                        "account_no": snapshot["account_no"],
                        "code": snapshot["code"],
                        "event_identity": snapshot["event_identity"],
                        "reconciliation_status": reconciliation["status"],
                        "manual_reconciliation_required": reconciliation["manual_reconciliation_required"],
                        "mismatch_fields": list(reconciliation["mismatch_fields"]),
                        "position_read_failure_reason": reconciliation["position_read_failure_reason"],
                        "before_sha256": before_sha256,
                        "after_sha256": after_sha256,
                        "blocked_reasons": [],
                        "warnings": list(snapshot.get("warnings") or []),
                    },
                    lock_acquired=True,
                    lock_wait_ms=lock.wait_ms,
                )
    except TimeoutError:
        return _with_lock_metadata(_blocked("broker_holding_lock", "broker holdings lock timeout"), lock_acquired=False)

    return _with_lock_metadata(_blocked("broker_holding_lock", "broker holdings lock failed"), lock_acquired=False)
