# -*- coding: utf-8 -*-
"""Automatic-close policy snapshot metadata.

This module is side-effect free. The caller reads the persistent policy and
the existing Runtime writer owns the resulting state.json mutation.
"""

from __future__ import annotations

from typing import Final


AUTO_CLOSE_RUNTIME_STATUSES: Final = frozenset(
    {"AUTO_CLOSE", "AUTO_CLOSING"}
)
AUTO_CLOSE_SNAPSHOT_KEYS: Final = (
    "auto_close_requested_at",
    "auto_close_source",
    "auto_close_method",
    "auto_close_policy",
)


def _text(value: object) -> str:
    return str(value or "").strip()


def auto_close_runtime_snapshot_active(state: dict[str, object] | None) -> bool:
    if not isinstance(state, dict):
        return False
    return bool(
        _text(state.get("auto_close_requested_at"))
        and _text(state.get("auto_close_method"))
        and isinstance(state.get("auto_close_policy"), dict)
        and state.get("auto_close_policy")
    )


def auto_close_runtime_snapshot_metadata(
    *,
    state: dict[str, object],
    before_status: object,
    after_status: object,
    auto_close_policy: dict[str, object],
    captured_at: str,
) -> dict[str, object]:
    """Return only the Runtime metadata that must change.

    Entering automatic close captures the persistent policy once. Repeated
    recalculation while automatic close remains active preserves that snapshot.
    Leaving automatic close clears the snapshot.
    """

    before = _text(before_status).upper()
    after = _text(after_status).upper()
    after_active = after in AUTO_CLOSE_RUNTIME_STATUSES
    has_snapshot_fields = any(
        key in state and state.get(key) not in ("", {}, None)
        for key in AUTO_CLOSE_SNAPSHOT_KEYS
    )

    if not after_active:
        if before in AUTO_CLOSE_RUNTIME_STATUSES or has_snapshot_fields:
            return {
                "auto_close_requested_at": "",
                "auto_close_source": "",
                "auto_close_method": "",
                "auto_close_policy": {},
            }
        return {}

    if auto_close_runtime_snapshot_active(state):
        return {}

    policy = dict(auto_close_policy) if isinstance(auto_close_policy, dict) else {}
    method = _text(policy.get("method")) or "루틴매도신호"
    policy["method"] = method
    return {
        "auto_close_requested_at": _text(captured_at),
        "auto_close_source": "TIME_POLICY",
        "auto_close_method": method,
        "auto_close_policy": policy,
    }
