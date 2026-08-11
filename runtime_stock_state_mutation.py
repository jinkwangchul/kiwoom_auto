# -*- coding: utf-8 -*-
"""Canonical mutation boundary for one stock Runtime ``state.json`` file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gui_auto_trade_runtime import now_text, write_state_json
from runtime_io import read_json_dict


@dataclass(frozen=True)
class RuntimeStockStateMutationResult:
    ok: bool
    before_status: str
    after_status: str
    read_back_verified: bool
    reason: str


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

    state["status"] = new_status
    state["updated_at"] = timestamp
    if metadata:
        state.update(metadata)

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
        if metadata:
            expected_values.update(metadata)
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
