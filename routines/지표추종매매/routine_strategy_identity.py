"""Routine-owned deterministic identities for Indicator Follow lifecycles."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def indicator_follow_cycle_identity(
    *,
    cycle: dict[str, Any] | None,
    signal: dict[str, Any] | None,
    runtime_context: dict[str, Any] | None,
    side: str,
) -> str | None:
    current = cycle if isinstance(cycle, dict) else {}
    existing = str(current.get("cycle_identity") or "").strip()
    if existing:
        return existing
    signal = signal if isinstance(signal, dict) else {}
    context = runtime_context if isinstance(runtime_context, dict) else {}
    routine_instance_id = str(
        context.get("routine_instance_id") or signal.get("routine_instance_id") or ""
    ).strip()
    code = str(context.get("code") or signal.get("code") or "").strip().lstrip("A")
    event_identity = next(
        (
            str(value).strip()
            for value in (
                context.get("trigger_commit_identity"),
                context.get("trigger_bar_identity"),
                context.get("trigger_bar_key"),
                context.get("tick_key"),
                signal.get("signal_input_hash"),
                signal.get("signal_bar_time"),
            )
            if str(value or "").strip()
        ),
        "",
    )
    if not routine_instance_id or not code or not event_identity:
        return None
    material = {
        "routine_type": "INDICATOR_FOLLOW",
        "routine_instance_id": routine_instance_id,
        "code": code,
        "side": str(side or "").strip().upper(),
        "event_identity": event_identity,
    }
    digest = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32].upper()
    return f"IF_CYCLE_{digest}"
