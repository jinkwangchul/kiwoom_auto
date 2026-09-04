# -*- coding: utf-8 -*-
"""Canonical mutation boundary for one stock Runtime ``state.json`` file."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from gui_auto_trade_runtime import now_text, write_state_json
from gui_review_utils import merge_existing_review_metadata
from runtime_atomic_writer import STATUS_OK, write_json_atomic
from runtime_io import read_json_dict


STATE_FIELD_DELETE_DELETED = "DELETED"
STATE_FIELD_DELETE_ALREADY_MISSING = "ALREADY_MISSING"
STATE_FIELD_DELETE_EXPECTED_HASH_MISMATCH = "EXPECTED_HASH_MISMATCH"
STATE_FIELD_DELETE_EXPECTED_FIELD_MISSING = "EXPECTED_FIELD_MISSING"
STATE_FIELD_DELETE_EXPECTED_FIELD_VALUE_MISMATCH = (
    "EXPECTED_FIELD_VALUE_MISMATCH"
)
STATE_FIELD_DELETE_STATE_UNAVAILABLE = "STATE_UNAVAILABLE"
STATE_FIELD_DELETE_STATE_INVALID = "STATE_INVALID"
STATE_FIELD_DELETE_INVALID_REQUEST = "INVALID_REQUEST"
STATE_FIELD_DELETE_WRITE_FAILED = "WRITE_FAILED"
STATE_FIELD_DELETE_READBACK_FAILED = "READBACK_FAILED"


class _ExpectedMissingRuntimeStockStateField:
    pass


RUNTIME_STOCK_STATE_EXPECTED_MISSING = _ExpectedMissingRuntimeStockStateField()
_STATE_FIELD_DELETE_LOCK = threading.RLock()


@dataclass(frozen=True)
class RuntimeStockStateMutationResult:
    ok: bool
    before_status: str
    after_status: str
    read_back_verified: bool
    reason: str


@dataclass(frozen=True)
class RuntimeStockStateFieldDeleteResult:
    ok: bool
    changed: bool
    reason_code: str
    stock_dir: str
    deleted_fields: tuple[str, ...]
    already_missing_fields: tuple[str, ...]
    before_sha256: str
    after_sha256: str
    readback_ok: bool


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _read_state_document(path: Path) -> tuple[dict[str, object] | None, bytes, str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return None, b"", STATE_FIELD_DELETE_STATE_UNAVAILABLE
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, raw, STATE_FIELD_DELETE_STATE_INVALID
    if not isinstance(value, dict):
        return None, raw, STATE_FIELD_DELETE_STATE_INVALID
    return value, raw, ""


def delete_runtime_stock_state_fields(
    stock_dir: str | Path,
    fields: Iterable[str],
    *,
    expected_file_sha256: str | None = None,
    expected_fields: Mapping[str, object] | None = None,
) -> RuntimeStockStateFieldDeleteResult:
    """Atomically delete only named fields from one existing ``state.json``.

    This boundary is intentionally separate from ``mutate_runtime_stock_state``:
    it never changes ``status``, ``updated_at``, or any non-target field.
    """

    target_dir = Path(stock_dir)
    state_path = target_dir / "state.json"
    normalized_fields = tuple(
        dict.fromkeys(str(field).strip() for field in fields if str(field).strip())
    )
    expected = dict(expected_fields or {})

    def result(
        *,
        ok: bool,
        changed: bool,
        reason_code: str,
        deleted_fields: tuple[str, ...] = (),
        already_missing_fields: tuple[str, ...] = (),
        before_sha256: str = "",
        after_sha256: str = "",
        readback_ok: bool = False,
    ) -> RuntimeStockStateFieldDeleteResult:
        return RuntimeStockStateFieldDeleteResult(
            ok=ok,
            changed=changed,
            reason_code=reason_code,
            stock_dir=str(target_dir),
            deleted_fields=deleted_fields,
            already_missing_fields=already_missing_fields,
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            readback_ok=readback_ok,
        )

    if (
        not normalized_fields
        or any(not isinstance(key, str) or not key.strip() for key in expected)
        or any(key not in normalized_fields for key in expected)
    ):
        return result(
            ok=False,
            changed=False,
            reason_code=STATE_FIELD_DELETE_INVALID_REQUEST,
        )

    with _STATE_FIELD_DELETE_LOCK:
        state, raw, read_reason = _read_state_document(state_path)
        before_sha256 = _sha256_bytes(raw) if raw else ""
        if state is None:
            return result(
                ok=False,
                changed=False,
                reason_code=read_reason,
                before_sha256=before_sha256,
            )

        expected_hash = str(expected_file_sha256 or "").strip().upper()
        if expected_hash and before_sha256 != expected_hash:
            return result(
                ok=False,
                changed=False,
                reason_code=STATE_FIELD_DELETE_EXPECTED_HASH_MISMATCH,
                before_sha256=before_sha256,
                after_sha256=before_sha256,
            )

        for key, expected_value in expected.items():
            if expected_value is RUNTIME_STOCK_STATE_EXPECTED_MISSING:
                if key in state:
                    return result(
                        ok=False,
                        changed=False,
                        reason_code=STATE_FIELD_DELETE_EXPECTED_FIELD_VALUE_MISMATCH,
                        before_sha256=before_sha256,
                        after_sha256=before_sha256,
                    )
                continue
            if key not in state:
                return result(
                    ok=False,
                    changed=False,
                    reason_code=STATE_FIELD_DELETE_EXPECTED_FIELD_MISSING,
                    before_sha256=before_sha256,
                    after_sha256=before_sha256,
                )
            if state[key] != expected_value:
                return result(
                    ok=False,
                    changed=False,
                    reason_code=STATE_FIELD_DELETE_EXPECTED_FIELD_VALUE_MISMATCH,
                    before_sha256=before_sha256,
                    after_sha256=before_sha256,
                )

        deleted_fields = tuple(field for field in normalized_fields if field in state)
        already_missing_fields = tuple(
            field for field in normalized_fields if field not in state
        )
        if not deleted_fields:
            return result(
                ok=True,
                changed=False,
                reason_code=STATE_FIELD_DELETE_ALREADY_MISSING,
                already_missing_fields=already_missing_fields,
                before_sha256=before_sha256,
                after_sha256=before_sha256,
                readback_ok=True,
            )

        next_state = dict(state)
        for field in deleted_fields:
            next_state.pop(field)

        current_state, current_raw, current_reason = _read_state_document(state_path)
        current_sha256 = _sha256_bytes(current_raw) if current_raw else ""
        if current_state is None:
            return result(
                ok=False,
                changed=False,
                reason_code=current_reason,
                before_sha256=before_sha256,
                after_sha256=current_sha256,
            )
        if current_sha256 != before_sha256 or current_state != state:
            return result(
                ok=False,
                changed=False,
                reason_code=STATE_FIELD_DELETE_EXPECTED_HASH_MISMATCH,
                before_sha256=before_sha256,
                after_sha256=current_sha256,
            )

        write_result = write_json_atomic(state_path, next_state)
        if (
            write_result.get("status") != STATUS_OK
            or write_result.get("written") is not True
        ):
            return result(
                ok=False,
                changed=False,
                reason_code=STATE_FIELD_DELETE_WRITE_FAILED,
                before_sha256=before_sha256,
            )

        readback, readback_raw, _readback_reason = _read_state_document(state_path)
        after_sha256 = _sha256_bytes(readback_raw) if readback_raw else ""
        readback_ok = readback == next_state and all(
            field not in readback for field in deleted_fields
        ) if isinstance(readback, dict) else False
        if not readback_ok:
            return result(
                ok=False,
                changed=True,
                reason_code=STATE_FIELD_DELETE_READBACK_FAILED,
                deleted_fields=deleted_fields,
                already_missing_fields=already_missing_fields,
                before_sha256=before_sha256,
                after_sha256=after_sha256,
                readback_ok=False,
            )

        return result(
            ok=True,
            changed=True,
            reason_code=STATE_FIELD_DELETE_DELETED,
            deleted_fields=deleted_fields,
            already_missing_fields=already_missing_fields,
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            readback_ok=True,
        )


def mutate_runtime_stock_state(
    stock_dir: str | Path,
    new_status: str,
    metadata: dict[str, object] | None = None,
    *,
    updated_at: str = "",
    verify_readback: bool = False,
    allow_review_state_transition: bool = False,
) -> RuntimeStockStateMutationResult:
    """Read, mutate, persist, and optionally verify one stock Runtime state."""

    path = Path(stock_dir)
    state = read_json_dict(path / "state.json")
    before_status = str(state.get("status", "STOPPED") or "STOPPED").strip().upper()
    timestamp = str(updated_at or "").strip() or now_text()

    mutation_metadata = dict(metadata or {})
    target_status = str(new_status or "").strip().upper()
    if target_status in {"REVIEW", "REVIEW_REQUIRED"} or (
        mutation_metadata.get("review_required") is True
    ):
        mutation_metadata = merge_existing_review_metadata(state, mutation_metadata)

    state["status"] = new_status
    state["updated_at"] = timestamp
    if mutation_metadata:
        state.update(mutation_metadata)

    after_status = str(state.get("status", "") or "").strip().upper()
    if not write_state_json(
        path,
        state,
        allow_review_state_transition=allow_review_state_transition,
    ):
        return RuntimeStockStateMutationResult(
            ok=False,
            before_status=before_status,
            after_status=after_status,
            read_back_verified=False,
            reason="WRITE_FAILED",
        )

    if verify_readback:
        saved_state = read_json_dict(path / "state.json")
        expected_values: dict[str, object] = {"status": new_status}
        if mutation_metadata:
            expected_values.update(mutation_metadata)
        if any(saved_state.get(key) != value for key, value in expected_values.items()):
            return RuntimeStockStateMutationResult(
                ok=False,
                before_status=before_status,
                after_status=after_status,
                read_back_verified=False,
                reason="READ_BACK_MISMATCH",
            )

    return RuntimeStockStateMutationResult(
        ok=True,
        before_status=before_status,
        after_status=after_status,
        read_back_verified=bool(verify_readback),
        reason="",
    )
