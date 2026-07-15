# -*- coding: utf-8 -*-
"""Update positions from one recorded fill using an explicit positions file.

This module only updates the provided positions_path. It does not modify fills,
order queues, broker APIs, Chejan handlers, GUI flows, timers, or realized P/L.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
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
NEXT_STAGE_ORDER_FILL_STATE_REVIEW_REQUIRED = "ORDER_FILL_STATE_REVIEW_REQUIRED"
FILL_RESULT_NEXT_STAGE_REQUIRED = "POSITION_UPDATE_REQUIRED"
_POSITION_THREAD_LOCK = threading.RLock()
_LOCK_POLL_SECONDS = 0.02
_DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "position_updated": False,
        "position_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": False,
        "position_write": False,
        "position_committed": False,
        "post_write_verified": False,
        "fill_delta_applied": 0,
        "lock_acquired": False,
        "lock_wait_ms": 0,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _noop(stage: str, reason: str) -> dict[str, Any]:
    return {
        "position_updated": False,
        "position_stage": stage,
        "next_stage": NEXT_STAGE_ORDER_FILL_STATE_REVIEW_REQUIRED,
        "changed": False,
        "position_write": False,
        "position_committed": False,
        "post_write_verified": True,
        "fill_delta_applied": 0,
        "blocked_reasons": [],
        "warnings": [reason],
    }


def _post_write_failed(stage: str, reason: str) -> dict[str, Any]:
    return {
        "position_updated": False,
        "position_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": True,
        "position_write": True,
        "position_committed": True,
        "post_write_verified": False,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _with_lock_metadata(result: dict[str, Any], *, lock_acquired: bool, lock_wait_ms: int = 0) -> dict[str, Any]:
    payload = dict(result)
    payload["lock_acquired"] = lock_acquired
    payload["lock_wait_ms"] = lock_wait_ms
    return payload


def _confirmed(context: Any) -> bool:
    return _as_dict(context).get("manual_position_update_confirmed") is True


def _snapshot_sha256(snapshot: Any) -> str:
    return _clean_text(_as_dict(snapshot).get("sha256")).upper()


def _lock_timeout_sec(context: Any) -> float:
    value = _as_dict(context).get("position_lock_timeout_sec")
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return _DEFAULT_LOCK_TIMEOUT_SECONDS


class _PositionFileLock:
    def __init__(self, target_path: Path, timeout_sec: float) -> None:
        self.lock_path = target_path.with_name(f"{target_path.name}.lock")
        self.timeout_sec = timeout_sec
        self.handle = None
        self.wait_ms = 0

    def __enter__(self) -> "_PositionFileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.lock_path.open("a+b")
        start = time.monotonic()
        while True:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                self.wait_ms = int((time.monotonic() - start) * 1000)
                return self
            except OSError:
                if time.monotonic() - start >= self.timeout_sec:
                    self.handle.close()
                    self.handle = None
                    raise TimeoutError("positions lock timeout")
                time.sleep(_LOCK_POLL_SECONDS)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.handle.close()
            self.handle = None


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


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
    if path and path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _read_positions(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not path.exists():
        return {"version": 1, "updated_at": None, "positions": []}, None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, _blocked("read_positions", f"failed to read positions json: {exc}")

    if not isinstance(data, dict):
        return {}, _blocked("read_positions", "positions root must be an object")

    positions = data.get("positions")
    if not isinstance(positions, list):
        return {}, _blocked("read_positions", "positions must be a list")

    for item in positions:
        if not isinstance(item, dict):
            return {}, _blocked("read_positions", "positions must contain only objects")

    return data, None


def _validate_fill_result(result_value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = _as_dict(result_value)
    if not isinstance(result_value, dict):
        return result, _blocked("fill_record_result", "fill_record_result must be a dict")

    if result.get("fill_recorded") is not True:
        return result, _blocked("fill_record_result", "fill_record_result.fill_recorded is not true")

    if result.get("next_stage") != FILL_RESULT_NEXT_STAGE_REQUIRED:
        return result, _blocked(
            "fill_record_result",
            "fill_record_result.next_stage is not POSITION_UPDATE_REQUIRED",
        )

    return result, None


def _required_text(record: dict[str, Any], field: str) -> str | None:
    if not _clean_text(record.get(field)):
        return f"fill_record.{field} is required"
    return None


def _required_int(record: dict[str, Any], field: str) -> str | None:
    if not isinstance(record.get(field), int):
        return f"fill_record.{field} is required"
    return None


def _validate_fill_record(fill_value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    record = _as_dict(fill_value)
    if not isinstance(fill_value, dict):
        return record, _blocked("fill_record", "fill_record must be a dict")

    for field in (
        "fill_id",
        "execution_identity_source",
        "execution_identity",
        "broker_order_no",
        "order_id",
        "order_queued_id",
        "execution_id",
        "broker",
        "account_no",
        "code",
        "side",
        "received_at",
    ):
        reason = _required_text(record, field)
        if reason:
            return record, _blocked("fill_record", reason)

    for field in ("filled_quantity", "filled_price", "order_quantity", "remaining_quantity"):
        reason = _required_int(record, field)
        if reason:
            return record, _blocked("fill_record", reason)

    if record["filled_quantity"] <= 0:
        return record, _blocked("quantity", "filled_quantity must be greater than 0")

    if record["filled_price"] <= 0:
        return record, _blocked("price", "filled_price must be greater than 0")

    side = _clean_text(record.get("side"))
    if side not in {"BUY", "SELL"}:
        return record, _blocked("side", "fill_record.side must be BUY or SELL")

    return record, None


def _position_id(record: dict[str, Any]) -> str:
    return "POSITION_{}_{}_{}".format(
        _clean_text(record.get("broker")),
        _clean_text(record.get("account_no")),
        _clean_text(record.get("code")),
    )


def _order_apply_key(record: dict[str, Any]) -> str:
    for field in ("order_queued_id", "order_id", "execution_id", "request_hash", "lock_id"):
        value = _clean_text(record.get(field))
        if value:
            return f"{field}:{value}"
    return ""


def _fill_identity(record: dict[str, Any]) -> tuple[str, str]:
    return _clean_text(record.get("execution_identity_source")), _clean_text(record.get("execution_identity"))


def _fill_identity_key(record: dict[str, Any]) -> str:
    source, value = _fill_identity(record)
    if source and value:
        return f"{source}:{value}"
    return ""


def _find_position(positions: list[Any], position_id: str) -> tuple[dict[str, Any] | None, int]:
    for index, position in enumerate(positions):
        item = _as_dict(position)
        if _clean_text(item.get("position_id")) == position_id:
            return item, index
    return None, -1


def _fill_already_applied(position: dict[str, Any], fill_id: str, identity_key: str) -> bool:
    applied_identities = position.get("applied_fill_identities")
    if identity_key and isinstance(applied_identities, list) and identity_key in applied_identities:
        return True
    applied = position.get("applied_fill_ids")
    if isinstance(applied, list) and fill_id in applied:
        return True
    return False


def _last_applied_cumulative(position: dict[str, Any], fill: dict[str, Any]) -> int:
    order_key = _order_apply_key(fill)
    audit = _as_dict(position.get("last_applied_cumulative_by_order"))
    value = audit.get(order_key)
    if isinstance(value, int):
        return value
    return 0


def _fill_delta(position: dict[str, Any], fill: dict[str, Any]) -> tuple[int, int, dict[str, Any] | None]:
    current_cumulative = fill["filled_quantity"]
    previous_cumulative = _last_applied_cumulative(position, fill)
    delta = current_cumulative - previous_cumulative
    if delta < 0:
        return previous_cumulative, delta, _blocked("out_of_order_fill", "filled_quantity is less than last applied cumulative quantity")
    return previous_cumulative, delta, None


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _json_number(value: Decimal) -> int | float:
    quantized = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return int(quantized)
    return float(quantized)


def _base_position(record: dict[str, Any], position_id: str, now: str) -> dict[str, Any]:
    return {
        "position_id": position_id,
        "broker": _clean_text(record.get("broker")),
        "account_no": _clean_text(record.get("account_no")),
        "code": _clean_text(record.get("code")),
        "side": "LONG",
        "quantity": 0,
        "average_price": 0,
        "cost_basis": 0,
        "position_status": "CLOSED",
        "last_fill_id": None,
        "last_fill_at": None,
        "applied_fill_ids": [],
        "updated_at": now,
    }


def _apply_buy(position: dict[str, Any], fill: dict[str, Any], fill_delta: int) -> tuple[dict[str, Any], Decimal, Decimal]:
    old_qty = _decimal(position.get("quantity", 0))
    old_avg = _decimal(position.get("average_price", 0))
    fill_qty = _decimal(fill_delta)
    fill_price = _decimal(fill["filled_price"])
    new_qty = old_qty + fill_qty
    new_cost = old_qty * old_avg + fill_qty * fill_price
    new_avg = new_cost / new_qty

    updated = deepcopy(position)
    updated["quantity"] = int(new_qty)
    updated["average_price"] = _json_number(new_avg)
    updated["cost_basis"] = _json_number(new_cost)
    updated["position_status"] = "OPEN"
    updated.pop("closed_at", None)
    return updated, old_qty, old_avg


def _apply_sell(position: dict[str, Any], fill: dict[str, Any], fill_delta: int) -> tuple[dict[str, Any], Decimal, Decimal, dict[str, Any] | None]:
    old_qty = _decimal(position.get("quantity", 0))
    old_avg = _decimal(position.get("average_price", 0))
    fill_qty = _decimal(fill_delta)
    if old_qty <= 0:
        return position, old_qty, old_avg, _blocked("position", "SELL requires an existing open position")
    if fill_qty > old_qty:
        return position, old_qty, old_avg, _blocked("quantity", "SELL filled_quantity exceeds position quantity")

    new_qty = old_qty - fill_qty
    new_cost = new_qty * old_avg
    updated = deepcopy(position)
    updated["quantity"] = int(new_qty)
    if new_qty == 0:
        updated["average_price"] = 0
        updated["cost_basis"] = 0
        updated["position_status"] = "CLOSED"
    else:
        updated["average_price"] = _json_number(old_avg)
        updated["cost_basis"] = _json_number(new_cost)
        updated["position_status"] = "OPEN"
    return updated, old_qty, old_avg, None


def update_position_from_fill(
    fill_record_result: Any,
    fill_record: Any,
    positions_path: str | Path | None,
    positions_snapshot: Any = None,
    context: Any = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Update one explicit positions file from one recorded fill."""
    result, result_blocked = _validate_fill_result(fill_record_result)
    if result_blocked is not None:
        return result_blocked

    fill, fill_blocked = _validate_fill_record(fill_record)
    if fill_blocked is not None:
        return fill_blocked

    if not _confirmed(context):
        return _blocked("operator_confirmation", "manual position update confirmation is required")

    if positions_path is None or not str(positions_path).strip():
        return _blocked("positions_path", "positions_path is required")

    target_path = Path(positions_path)
    before_sha256 = None
    if target_path.exists():
        before_sha256 = _sha256_file(target_path)

    snapshot_sha256 = _snapshot_sha256(positions_snapshot)
    if snapshot_sha256 and before_sha256 != snapshot_sha256:
        return _blocked(
            "stale_positions",
            "positions file changed after fill record; manual review required",
        )
    try:
        with _POSITION_THREAD_LOCK:
            with _PositionFileLock(target_path, _lock_timeout_sec(context)) as lock:
                current_sha256 = _sha256_file(target_path) if target_path.exists() else None
                before_sha256 = current_sha256
                if snapshot_sha256 and current_sha256 != snapshot_sha256:
                    return _with_lock_metadata(
                        _blocked("stale_positions", "positions file changed after fill record; manual review required"),
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                data, read_blocked = _read_positions(target_path)
                if read_blocked is not None:
                    return _with_lock_metadata(read_blocked, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                fill_id = _clean_text(fill.get("fill_id"))
                fill_identity_key = _fill_identity_key(fill)
                if not fill_identity_key:
                    return _with_lock_metadata(
                        _blocked("fill_identity", "execution identity is required for position idempotency"),
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                now = _now_text()
                position_id = _position_id(fill)
                positions = data["positions"]
                existing_position, position_index = _find_position(positions, position_id)
                if existing_position is None:
                    if _clean_text(fill.get("side")) == "SELL":
                        return _with_lock_metadata(
                            _blocked("position", "SELL requires an existing open position"),
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )
                    working_position = _base_position(fill, position_id, now)
                else:
                    working_position = deepcopy(existing_position)

                if _fill_already_applied(working_position, fill_id, fill_identity_key):
                    payload = _noop("duplicate_fill", "fill already applied to position")
                    payload.update(
                        {
                            "fill_id": fill_id,
                            "positions_path": str(target_path),
                            "position_id": position_id,
                            "before_sha256": before_sha256,
                        }
                    )
                    return _with_lock_metadata(payload, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                previous_cumulative, fill_delta, delta_blocked = _fill_delta(working_position, fill)
                if delta_blocked is not None:
                    delta_blocked.update(
                        {
                            "position_id": position_id,
                            "fill_id": fill_id,
                            "previous_applied_filled_quantity": previous_cumulative,
                            "current_filled_quantity": fill["filled_quantity"],
                            "fill_delta_applied": 0,
                        }
                    )
                    return _with_lock_metadata(delta_blocked, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                if fill_delta == 0:
                    payload = _noop("fill_delta_noop", "filled_quantity was already applied to position")
                    payload.update(
                        {
                            "positions_path": str(target_path),
                            "position_id": position_id,
                            "fill_id": fill_id,
                            "previous_applied_filled_quantity": previous_cumulative,
                            "current_filled_quantity": fill["filled_quantity"],
                            "before_sha256": before_sha256,
                        }
                    )
                    return _with_lock_metadata(payload, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                side = _clean_text(fill.get("side"))
                if side == "BUY":
                    updated_position, old_qty, old_avg = _apply_buy(working_position, fill, fill_delta)
                else:
                    updated_position, old_qty, old_avg, sell_blocked = _apply_sell(working_position, fill, fill_delta)
                    if sell_blocked is not None:
                        return _with_lock_metadata(sell_blocked, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                applied = updated_position.get("applied_fill_ids")
                if not isinstance(applied, list):
                    applied = []
                applied_identities = updated_position.get("applied_fill_identities")
                if not isinstance(applied_identities, list):
                    applied_identities = []
                cumulative_by_order = _as_dict(updated_position.get("last_applied_cumulative_by_order"))
                order_key = _order_apply_key(fill)
                cumulative_by_order[order_key] = fill["filled_quantity"]

                updated_position["applied_fill_ids"] = applied + [fill_id]
                updated_position["applied_fill_identities"] = applied_identities + [fill_identity_key]
                updated_position["last_applied_cumulative_by_order"] = cumulative_by_order
                updated_position["last_applied_filled_quantity"] = fill["filled_quantity"]
                updated_position["last_applied_fill_delta"] = fill_delta
                updated_position["last_fill_identity_source"] = _clean_text(fill.get("execution_identity_source"))
                updated_position["last_fill_identity"] = _clean_text(fill.get("execution_identity"))
                updated_position["last_fill_id"] = fill_id
                updated_position["last_fill_at"] = _clean_text(fill.get("received_at"))
                updated_position["updated_at"] = now
                if updated_position.get("position_status") == "CLOSED":
                    updated_position["closed_at"] = now

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

                updated_data = deepcopy(data)
                updated_data["version"] = updated_data.get("version", 1)
                updated_data["updated_at"] = now
                if position_index >= 0:
                    updated_data["positions"][position_index] = updated_position
                else:
                    updated_data["positions"].append(updated_position)

                tmp_path = None
                try:
                    tmp_path = _write_json_temp(target_path, updated_data)
                    os.replace(tmp_path, target_path)
                    tmp_path = None
                except Exception as exc:
                    _cleanup_temp(tmp_path)
                    return _with_lock_metadata(
                        _blocked("write_positions", f"failed to write positions json: {exc}"),
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                after_sha256 = _sha256_file(target_path)
                post_data, post_blocked = _read_positions(target_path)
                if post_blocked is not None:
                    failed = _post_write_failed(
                        "post_write_verify",
                        post_blocked.get("blocked_reasons", ["positions json invalid after write"])[0],
                    )
                    failed.update(
                        {
                            "positions_path": str(target_path),
                            "backup_path": backup_path,
                            "position_id": position_id,
                            "fill_id": fill_id,
                            "before_sha256": before_sha256,
                            "after_sha256": after_sha256,
                            "fill_delta_applied": fill_delta,
                        }
                    )
                    return _with_lock_metadata(failed, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                if post_data != updated_data:
                    failed = _post_write_failed("post_write_verify", "positions json did not match expected data after write")
                    failed.update(
                        {
                            "positions_path": str(target_path),
                            "backup_path": backup_path,
                            "position_id": position_id,
                            "fill_id": fill_id,
                            "before_sha256": before_sha256,
                            "after_sha256": after_sha256,
                            "fill_delta_applied": fill_delta,
                        }
                    )
                    return _with_lock_metadata(failed, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                result_payload = {
                    "position_updated": True,
                    "position_stage": "position_updated_from_fill",
                    "next_stage": NEXT_STAGE_ORDER_FILL_STATE_REVIEW_REQUIRED,
                    "changed": True,
                    "position_write": True,
                    "position_committed": True,
                    "post_write_verified": True,
                    "positions_path": str(target_path),
                    "backup_path": backup_path,
                    "position_id": position_id,
                    "fill_id": fill_id,
                    "execution_identity_source": _clean_text(fill.get("execution_identity_source")),
                    "execution_identity": _clean_text(fill.get("execution_identity")),
                    "code": _clean_text(fill.get("code")),
                    "side": side,
                    "previous_applied_filled_quantity": previous_cumulative,
                    "current_filled_quantity": fill["filled_quantity"],
                    "fill_delta_applied": fill_delta,
                    "previous_quantity": int(old_qty),
                    "new_quantity": updated_position["quantity"],
                    "previous_average_price": _json_number(old_avg),
                    "new_average_price": updated_position["average_price"],
                    "before_quantity": int(old_qty),
                    "after_quantity": updated_position["quantity"],
                    "before_average_price": _json_number(old_avg),
                    "after_average_price": updated_position["average_price"],
                    "before_sha256": before_sha256,
                    "after_sha256": after_sha256,
                    "blocked_reasons": [],
                    "warnings": [],
                }
                return _with_lock_metadata(result_payload, lock_acquired=True, lock_wait_ms=lock.wait_ms)
    except TimeoutError:
        return _with_lock_metadata(_blocked("position_lock", "positions lock timeout"), lock_acquired=False)

    return _with_lock_metadata(_blocked("position_lock", "positions lock failed"), lock_acquired=False)
