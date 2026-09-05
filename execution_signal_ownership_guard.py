# -*- coding: utf-8 -*-
"""Read-only final dispatch fence for superseded routine signals."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parent
SIGNALS_PATH = PROJECT_ROOT / "runtime" / "routine_signals.json"
_TERMINAL = {"DONE", "CANCELLED", "EXPIRED", "ERROR", "BLOCKED"}


def signal_dispatch_block_reasons(
    order: Mapping[str, Any] | object,
    *,
    signals_path: str | Path = SIGNALS_PATH,
) -> list[str]:
    """Fail closed unless the owning signal is current and progression-safe."""
    if not isinstance(order, Mapping):
        return ["SIGNAL_OWNERSHIP_ORDER_INVALID"]
    signal_id = str(order.get("source_signal_id") or "").strip()
    # Legacy/manual order paths have no routine-signal ownership contract.
    if not signal_id:
        return []
    try:
        root = json.loads(Path(signals_path).read_text(encoding="utf-8"))
    except Exception:
        return ["SIGNAL_OWNERSHIP_READ_FAILED"]
    signals = root.get("signals") if isinstance(root, dict) else None
    if not isinstance(signals, list):
        return ["SIGNAL_OWNERSHIP_SCHEMA_INVALID"]
    matches = [row for row in signals if isinstance(row, dict) and str(row.get("id") or "").strip() == signal_id]
    if len(matches) != 1:
        return ["SIGNAL_OWNERSHIP_IDENTITY_INVALID"]
    signal = matches[0]
    status = str(signal.get("status") or "").strip().upper()
    if status in _TERMINAL:
        return [f"SIGNAL_PROCESS_TERMINAL:{status}"]
    if signal.get("supersede_pending") is True:
        return ["SIGNAL_PROCESS_SUPERSEDE_PENDING"]
    return []


__all__ = ["signal_dispatch_block_reasons"]
