# -*- coding: utf-8 -*-
"""Generic Runtime contract for running base-budget adjustments.

The state machine observes only standard BUY/SELL signals.  It deliberately
does not interpret strategy, cycle, or position state.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from runtime_io import read_json_dict
from runtime_stock_state_mutation import mutate_runtime_stock_state


ADJUSTMENT_KEY = "running_budget_adjustment"
POLICY_IMMEDIATE = "IMMEDIATE"
POLICY_NEXT_CYCLE = "NEXT_CYCLE"
STATE_WAIT_FIRST_BUY = "WAIT_FIRST_BUY"
STATE_WAIT_SELL = "WAIT_SELL"
STATE_APPLIED = "APPLIED"
_VALID_POLICIES = frozenset({POLICY_IMMEDIATE, POLICY_NEXT_CYCLE})
_ACTIVE_STATES = frozenset(
    {STATE_WAIT_FIRST_BUY, STATE_WAIT_SELL, STATE_APPLIED}
)


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _mode(value: object) -> str:
    return "AMOUNT" if str(value or "").strip().upper() == "AMOUNT" else "QUANTITY"


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 < parsed <= 99_999_999 else None


def _nonnegative_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 99_999_999 else None


def _session_identity(state: Mapping[str, object]) -> str:
    return str(state.get("trade_started_at") or "").strip()


def _active_adjustment(
    state: Mapping[str, object],
) -> dict[str, object] | None:
    raw = state.get(ADJUSTMENT_KEY)
    if not isinstance(raw, dict):
        return None
    adjustment = dict(raw)
    if str(adjustment.get("state") or "").strip().upper() not in _ACTIVE_STATES:
        return None
    session_identity = _session_identity(state)
    if not session_identity or not bool(state.get("trade_enabled", False)):
        return None
    if str(adjustment.get("operation_session_started_at") or "").strip() != session_identity:
        return None
    return adjustment


def project_running_budget_adjustment_config(
    config: Mapping[str, object],
    state: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Project the effective budget input without mutating base config."""

    projected = deepcopy(dict(config))
    adjustment = _active_adjustment(state)
    if adjustment is None:
        return projected, {"active": False, "applied": False, "reason": "NO_ACTIVE_ADJUSTMENT"}

    adjustment_state = str(adjustment.get("state") or "").strip().upper()
    if adjustment_state == STATE_WAIT_SELL:
        previous_value = _nonnegative_int(adjustment.get("previous_value"))
        if previous_value is not None:
            mode = _mode(adjustment.get("mode"))
            if mode != _mode(projected.get("trade_amount_type")):
                return projected, {
                    "active": True,
                    "applied": False,
                    "state": adjustment_state,
                    "reason": "MODE_MISMATCH",
                }
            projected["buy_amount" if mode == "AMOUNT" else "buy_qty"] = previous_value
            if bool(adjustment.get("apply_limit", False)):
                previous_limit = adjustment.get("previous_limit")
                if isinstance(previous_limit, dict):
                    for key in (
                        "buy_limit_enabled",
                        "buy_limit_amount",
                        "buy_limit_source",
                    ):
                        if key in previous_limit:
                            projected[key] = previous_limit.get(key)
                        else:
                            projected.pop(key, None)
        return projected, {
            "active": True,
            "applied": False,
            "state": adjustment_state,
            "request_id": str(adjustment.get("request_id") or ""),
            "reason": "WAITING_FOR_SELL",
        }

    mode = _mode(adjustment.get("mode"))
    if mode != _mode(projected.get("trade_amount_type")):
        return projected, {
            "active": True,
            "applied": False,
            "state": adjustment_state,
            "reason": "MODE_MISMATCH",
        }
    requested_value = _positive_int(adjustment.get("requested_value"))
    if requested_value is None:
        return projected, {
            "active": True,
            "applied": False,
            "state": adjustment_state,
            "reason": "INVALID_REQUESTED_VALUE",
        }

    projected["trade_amount_type"] = mode
    projected["buy_amount" if mode == "AMOUNT" else "buy_qty"] = requested_value
    if bool(adjustment.get("apply_limit", False)):
        limit_amount = _positive_int(adjustment.get("adjusted_limit_amount"))
        if limit_amount is None:
            return dict(config), {
                "active": True,
                "applied": False,
                "state": adjustment_state,
                "reason": "INVALID_LIMIT_PROJECTION",
            }
        projected["buy_limit_enabled"] = True
        projected["buy_limit_amount"] = limit_amount
        projected["buy_limit_source"] = "RECOMMENDED"

    return projected, {
        "active": True,
        "applied": True,
        "state": adjustment_state,
        "request_id": str(adjustment.get("request_id") or ""),
        "mode": mode,
        "requested_value": requested_value,
        "apply_limit": bool(adjustment.get("apply_limit", False)),
        "reason": "RUNTIME_BUDGET_PROJECTED",
    }


def project_running_budget_adjustment_display_config(
    config: Mapping[str, object],
    state: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Project the confirmed request for immediate operator-facing display."""

    projected = deepcopy(dict(config))
    adjustment = _active_adjustment(state)
    if adjustment is None:
        return projected, {"active": False, "reason": "NO_ACTIVE_ADJUSTMENT"}

    mode = _mode(adjustment.get("mode"))
    if mode != _mode(projected.get("trade_amount_type")):
        return projected, {
            "active": True,
            "hydrated": False,
            "reason": "MODE_MISMATCH",
        }
    requested_value = _positive_int(adjustment.get("requested_value"))
    if requested_value is None:
        return projected, {
            "active": True,
            "hydrated": False,
            "reason": "INVALID_REQUESTED_VALUE",
        }

    projected["trade_amount_type"] = mode
    projected["buy_amount" if mode == "AMOUNT" else "buy_qty"] = requested_value
    if bool(adjustment.get("apply_limit", False)):
        limit_amount = _positive_int(adjustment.get("adjusted_limit_amount"))
        if limit_amount is not None:
            projected["buy_limit_enabled"] = True
            projected["buy_limit_amount"] = limit_amount
            projected["buy_limit_source"] = "RECOMMENDED"
    return projected, {
        "active": True,
        "hydrated": True,
        "state": str(adjustment.get("state") or "").strip().upper(),
        "request_id": str(adjustment.get("request_id") or ""),
        "adjustment": deepcopy(adjustment),
        "reason": "PENDING_REQUEST_DISPLAYED",
    }


def commit_running_budget_adjustment(
    stock_dir: str | Path,
    *,
    stock_code: object,
    expected_mode: object,
    requested_value: object,
    apply_policy: object,
    apply_limit: bool,
    adjusted_limit_amount: object = None,
    requested_at: object = None,
    confirmed_at: str = "",
) -> dict[str, object]:
    """Persist one verified request through the existing stock Runtime writer."""

    target_dir = Path(stock_dir)
    config = read_json_dict(target_dir / "config.json")
    state = read_json_dict(target_dir / "state.json")
    mode = _mode(expected_mode)
    value = _positive_int(requested_value)
    policy = str(apply_policy or "").strip().upper()
    limit_amount = _positive_int(adjusted_limit_amount) if apply_limit else None
    session_identity = _session_identity(state)
    previous_value = _nonnegative_int(
        config.get("buy_amount" if mode == "AMOUNT" else "buy_qty")
    )

    if mode != _mode(config.get("trade_amount_type")):
        return {"ok": False, "reason": "MODE_CHANGED"}
    if value is None:
        return {"ok": False, "reason": "INVALID_REQUESTED_VALUE"}
    if policy not in _VALID_POLICIES:
        return {"ok": False, "reason": "INVALID_APPLY_POLICY"}
    if previous_value is None:
        return {"ok": False, "reason": "INVALID_CURRENT_VALUE"}
    if not bool(state.get("trade_enabled", False)) or not session_identity:
        return {"ok": False, "reason": "OPERATION_SESSION_NOT_ACTIVE"}
    if apply_limit and limit_amount is None:
        return {"ok": False, "reason": "LIMIT_EVIDENCE_UNAVAILABLE"}

    timestamp = str(confirmed_at or "").strip() or _now_text()
    request_timestamp = str(requested_at or "").strip() or timestamp
    adjustment = {
        "version": 2,
        "request_id": uuid4().hex,
        "stock_code": str(stock_code or "").strip().upper().lstrip("A"),
        "mode": mode,
        "requested_value": value,
        "previous_value": previous_value,
        "apply_policy": policy,
        "state": (
            STATE_WAIT_FIRST_BUY
            if policy == POLICY_IMMEDIATE
            else STATE_WAIT_SELL
        ),
        "apply_limit": bool(apply_limit),
        "adjusted_limit_amount": limit_amount,
        "previous_limit": {
            key: config.get(key)
            for key in (
                "buy_limit_enabled",
                "buy_limit_amount",
                "buy_limit_source",
            )
        },
        "requested_at": request_timestamp,
        "confirmed_at": timestamp,
        "operation_session_started_at": session_identity,
    }
    result = mutate_runtime_stock_state(
        target_dir,
        str(state.get("status") or "STOPPED"),
        {ADJUSTMENT_KEY: adjustment},
        updated_at=timestamp,
        verify_readback=True,
    )
    return {
        "ok": bool(result.ok and result.read_back_verified),
        "reason": result.reason,
        "adjustment": adjustment,
        "writer": "runtime_stock_state_mutation.mutate_runtime_stock_state",
    }


def transition_running_budget_adjustment_for_signal(
    stock_dir: str | Path,
    *,
    signal: object,
    signal_id: object,
    observed_at: str = "",
) -> dict[str, object]:
    """Advance adjustment state for one newly queued standard signal."""

    target_dir = Path(stock_dir)
    normalized_signal = str(signal or "").strip().upper()
    clean_signal_id = str(signal_id or "").strip()
    if normalized_signal not in {"BUY", "SELL"} or not clean_signal_id:
        return {"ok": True, "changed": False, "reason": "SIGNAL_NOT_QUEUED"}

    state = read_json_dict(target_dir / "state.json")
    adjustment = _active_adjustment(state)
    if adjustment is None:
        return {"ok": True, "changed": False, "reason": "NO_ACTIVE_ADJUSTMENT"}

    before = str(adjustment.get("state") or "").strip().upper()
    after = before
    if before == STATE_WAIT_SELL and normalized_signal == "SELL":
        after = STATE_WAIT_FIRST_BUY
    elif before == STATE_WAIT_FIRST_BUY and normalized_signal == "BUY":
        after = STATE_APPLIED
    if after == before:
        return {
            "ok": True,
            "changed": False,
            "before": before,
            "after": after,
            "reason": "SIGNAL_DOES_NOT_ADVANCE_STATE",
        }

    timestamp = str(observed_at or "").strip() or _now_text()
    next_adjustment = dict(adjustment)
    next_adjustment["state"] = after
    next_adjustment["last_transition_at"] = timestamp
    next_adjustment["last_transition_signal"] = normalized_signal
    next_adjustment["last_transition_signal_id"] = clean_signal_id
    if after == STATE_WAIT_FIRST_BUY:
        next_adjustment["sell_observed_at"] = timestamp
        next_adjustment["sell_signal_id"] = clean_signal_id
    elif after == STATE_APPLIED:
        next_adjustment["applied_at"] = timestamp
        next_adjustment["applied_signal_id"] = clean_signal_id

    result = mutate_runtime_stock_state(
        target_dir,
        str(state.get("status") or "STOPPED"),
        {ADJUSTMENT_KEY: next_adjustment},
        updated_at=timestamp,
        verify_readback=True,
    )
    return {
        "ok": bool(result.ok and result.read_back_verified),
        "changed": bool(result.ok and result.read_back_verified),
        "before": before,
        "after": after,
        "reason": result.reason,
        "request_id": str(next_adjustment.get("request_id") or ""),
    }


def running_budget_adjustment_snapshot(
    stock_dir: str | Path,
) -> dict[str, Any]:
    """Return a read-only copy for diagnostics and focused tests."""

    state = read_json_dict(Path(stock_dir) / "state.json")
    raw = state.get(ADJUSTMENT_KEY)
    return deepcopy(raw) if isinstance(raw, dict) else {}
