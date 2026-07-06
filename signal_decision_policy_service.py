"""Read-only signal decision policy preview.

This module applies the first policy layer to a Signal Decision Preview.
Current policy is intentionally empty: valid ACCEPT and IGNORE decisions pass.
It never writes runtime files, never enqueues signals, and never calls
execution or order adapters.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from time_policy import deep_merge_defaults, seconds_from_hhmmss


STAGE = "SIGNAL_POLICY_PREVIEW"
DECISION_ACCEPT = "ACCEPT"
DECISION_REJECT = "REJECT"
DECISION_IGNORE = "IGNORE"
POLICY_PASS = "PASS"
POLICY_REJECT = "REJECT"
TIME_POLICY_REGULAR = "REGULAR"
OPERATION_POLICY_STATE = "OPERATION_STATE"
OPERATION_STATUS_RUNNING = "RUNNING"
OPERATION_STATUS_STOPPED = "STOPPED"
OPERATION_STATUS_PAUSED = "PAUSED"
OPERATION_STATUS_EMERGENCY_STOP = "EMERGENCY_STOP"
ROUTINE_POLICY_ACTIVE = "ROUTINE_ACTIVE"
STOCK_POLICY_ACTIVE = "STOCK_ACTIVE"
EMERGENCY_POLICY_DETAIL = "EMERGENCY_DETAIL"
BUDGET_POLICY = "BUDGET"
DUPLICATE_POLICY_SIGNAL = "DUPLICATE_SIGNAL"
COOLDOWN_POLICY = "COOLDOWN"
DELAY_POLICY = "DELAY"
POLICY_IGNORE = "IGNORE"
POLICY_ORCHESTRATOR = "SIGNAL_POLICY_ORCHESTRATOR"
ORCHESTRATED_POLICY_STEPS = (
    "TIME_POLICY",
    "OPERATION_STATE_POLICY",
    "ROUTINE_ACTIVE_POLICY",
    "STOCK_ACTIVE_POLICY",
    "EMERGENCY_DETAIL_POLICY",
    "BUDGET_POLICY",
    "DUPLICATE_SIGNAL_POLICY",
    "COOLDOWN_POLICY",
    "DELAY_POLICY",
)
ACTIVE_STATUS = "ACTIVE"
REJECT_ROUTINE_STATUSES = {"INACTIVE", "PAUSED", "STOPPED"}
REJECT_STOCK_STATUSES = {"INACTIVE", "PAUSED", "STOPPED", "REVIEW"}
SUPPORTED_OPERATION_STATUSES = {
    OPERATION_STATUS_RUNNING,
    OPERATION_STATUS_STOPPED,
    OPERATION_STATUS_PAUSED,
    OPERATION_STATUS_EMERGENCY_STOP,
}
REQUIRED_DECISION_FIELDS = (
    "ok",
    "stage",
    "decision",
    "signal",
    "reason",
    "decision_reason",
    "rule_source",
    "matched_rule_paths",
    "condition_summary",
)


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _copy_policy_preview(policy_preview: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(policy_preview)
    result["queue_connected"] = False
    result["runtime_write"] = False
    result["execution_connected"] = False
    result["send_order_connected"] = False
    return result


def _now_from_inputs(
    now: datetime | None,
    datetime_provider: Any = None,
) -> datetime | None:
    if now is not None:
        return now
    if datetime_provider is None:
        return None
    value = datetime_provider()
    return value if isinstance(value, datetime) else None


def _is_regular_time(now_dt: datetime) -> bool:
    config = deep_merge_defaults(None)
    regular = config["regular_market"]
    now_s = now_dt.hour * 3600 + now_dt.minute * 60 + now_dt.second
    start_s = seconds_from_hhmmss(regular["start_time"], "09:00:00")
    end_s = seconds_from_hhmmss(regular["realtime_end_time"], "15:20:00")
    return start_s <= now_s <= end_s


def _operation_status(operation_state: dict[str, Any]) -> str:
    return str(operation_state.get("operation_status") or "").strip().upper()


def _status(state: dict[str, Any]) -> str:
    return str(state.get("status") or "").strip().upper()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _base_result(
    *,
    ok: bool,
    decision: str,
    policy_result: str,
    policy_reason: str,
    signal: str | None,
    reason: Any = None,
    decision_reason: Any = None,
    rule_source: Any = None,
    matched_rule_paths: Any = None,
    condition_summary: Any = None,
    blocked_reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "stage": STAGE,
        "decision": decision,
        "policy_result": policy_result,
        "policy_reason": policy_reason,
        "signal": signal,
        "reason": deepcopy(reason),
        "decision_reason": deepcopy(decision_reason),
        "rule_source": deepcopy(rule_source),
        "matched_rule_paths": _as_list(matched_rule_paths),
        "condition_summary": _as_list(condition_summary),
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
        "blocked_reasons": deepcopy(blocked_reasons or []),
        "warnings": [],
    }


def _reject(reason: str, decision_preview: dict[str, Any] | None = None) -> dict[str, Any]:
    source = decision_preview if isinstance(decision_preview, dict) else {}
    signal = source.get("signal")
    if signal not in {"BUY", "SELL", None}:
        signal = None
    return _base_result(
        ok=False,
        decision=DECISION_REJECT,
        policy_result=POLICY_REJECT,
        policy_reason=reason,
        signal=signal,
        reason=source.get("reason"),
        decision_reason=source.get("decision_reason"),
        rule_source=source.get("rule_source"),
        matched_rule_paths=source.get("matched_rule_paths"),
        condition_summary=source.get("condition_summary"),
        blocked_reasons=[reason],
    )


def _validate_policy_preview(policy_preview: dict[str, Any]) -> str | None:
    if not isinstance(policy_preview, dict):
        return "policy_preview must be dict"
    if policy_preview.get("ok") is not True:
        return "policy_preview.ok is not true"
    if policy_preview.get("stage") != STAGE:
        return "policy_preview.stage is invalid"
    if policy_preview.get("policy_result") != POLICY_PASS:
        return "policy_preview.policy_result is not PASS"
    return None


def _missing_fields(state: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if field not in state]


def _with_orchestrator_result(
    result: dict[str, Any],
    *,
    applied_policies: list[str],
    blocked_policy: str | None,
) -> dict[str, Any]:
    final = _copy_policy_preview(result)
    final["policy_orchestrator"] = POLICY_ORCHESTRATOR
    final["applied_policies"] = list(applied_policies)
    final["blocked_policy"] = blocked_policy

    if blocked_policy is not None or final.get("ok") is not True or final.get("policy_result") == POLICY_REJECT:
        final["policy_orchestrator_result"] = POLICY_REJECT
        final["policy_orchestrator_reason"] = (
            f"{blocked_policy} rejected signal policy preview"
            if blocked_policy
            else "signal policy preview rejected"
        )
    elif final.get("decision") == DECISION_IGNORE:
        final["policy_orchestrator_result"] = POLICY_IGNORE
        final["policy_orchestrator_reason"] = "decision is IGNORE and all applied policies passed"
    else:
        final["policy_orchestrator_result"] = POLICY_PASS
        final["policy_orchestrator_reason"] = "all signal policies passed"

    return final


def build_signal_policy_preview(decision_preview: dict[str, Any]) -> dict[str, Any]:
    """Apply the current no-op policy layer to a Signal Decision Preview."""
    if not isinstance(decision_preview, dict):
        return _reject("decision_preview must be dict")

    missing = [
        field
        for field in REQUIRED_DECISION_FIELDS
        if field not in decision_preview
    ]
    if missing:
        return _reject(
            "missing required decision_preview fields: " + ", ".join(missing),
            decision_preview,
        )

    if decision_preview.get("ok") is not True:
        return _reject("decision_preview.ok is not true", decision_preview)

    if decision_preview.get("stage") != "SIGNAL_DECISION_PREVIEW":
        return _reject("decision_preview.stage is invalid", decision_preview)

    decision = decision_preview.get("decision")
    signal = decision_preview.get("signal")
    if decision == DECISION_ACCEPT and signal not in {"BUY", "SELL"}:
        return _reject("decision_preview.ACCEPT requires BUY or SELL signal", decision_preview)
    if decision == DECISION_IGNORE and signal is not None:
        return _reject("decision_preview.IGNORE requires signal None", decision_preview)
    if decision not in {DECISION_ACCEPT, DECISION_IGNORE}:
        return _reject("decision_preview.decision is invalid", decision_preview)

    return _base_result(
        ok=True,
        decision=decision,
        policy_result=POLICY_PASS,
        policy_reason="current signal policy allows valid decision preview",
        signal=signal,
        reason=decision_preview.get("reason"),
        decision_reason=decision_preview.get("decision_reason"),
        rule_source=decision_preview.get("rule_source"),
        matched_rule_paths=decision_preview.get("matched_rule_paths"),
        condition_summary=decision_preview.get("condition_summary"),
    )


def apply_signal_policy(decision_preview: dict[str, Any]) -> dict[str, Any]:
    """Alias for call sites that prefer verb-first naming."""
    return build_signal_policy_preview(decision_preview)


def apply_time_policy(
    policy_preview: dict[str, Any],
    market_snapshot: dict[str, Any],
    *,
    now: datetime | None = None,
    datetime_provider: Any = None,
) -> dict[str, Any]:
    """Apply regular-market time policy to a Policy Preview."""
    preview_error = _validate_policy_preview(policy_preview)
    if preview_error:
        return _reject(preview_error, policy_preview)
    if not isinstance(market_snapshot, dict):
        return _reject("market_snapshot must be dict", policy_preview)
    if "timeframe" not in market_snapshot:
        return _reject("market_snapshot.timeframe is required", policy_preview)

    now_dt = _now_from_inputs(now, datetime_provider)
    if now_dt is None:
        return _reject("now or datetime_provider is required", policy_preview)

    result = _copy_policy_preview(policy_preview)
    result["time_policy"] = TIME_POLICY_REGULAR
    if _is_regular_time(now_dt):
        result["time_policy_result"] = POLICY_PASS
        result["time_policy_reason"] = "regular market time policy passed"
        result["blocked_reasons"] = []
        return result

    result["ok"] = False
    result["decision"] = DECISION_REJECT
    result["policy_result"] = POLICY_REJECT
    result["policy_reason"] = "regular market time policy rejected"
    result["time_policy_result"] = POLICY_REJECT
    result["time_policy_reason"] = "outside regular market time: 09:00:00-15:20:00"
    result["blocked_reasons"] = [result["time_policy_reason"]]
    return result


def apply_operation_state_policy(
    policy_preview: dict[str, Any],
    operation_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply injected operation-state policy to a Policy Preview."""
    preview_error = _validate_policy_preview(policy_preview)
    if preview_error:
        return _reject(preview_error, policy_preview)
    if not isinstance(operation_state, dict):
        return _reject("operation_state must be dict", policy_preview)

    missing = _missing_fields(operation_state, ("enabled", "emergency_stop", "operation_status"))
    if missing:
        return _reject("missing required operation_state fields: " + ", ".join(missing), policy_preview)

    result = _copy_policy_preview(policy_preview)
    result["operation_policy"] = OPERATION_POLICY_STATE
    status = _operation_status(operation_state)
    enabled = operation_state.get("enabled") is True
    emergency_stop = operation_state.get("emergency_stop") is True

    if not enabled:
        operation_reason = "operation_state.enabled is not true"
    elif emergency_stop:
        operation_reason = "operation_state.emergency_stop is true"
    elif status not in SUPPORTED_OPERATION_STATUSES:
        operation_reason = "operation_state.operation_status is invalid"
    elif status != OPERATION_STATUS_RUNNING:
        operation_reason = f"operation_state.operation_status is {status}"
    else:
        result["operation_policy_result"] = POLICY_PASS
        result["operation_policy_reason"] = "operation state policy passed"
        result["blocked_reasons"] = []
        return result

    result["ok"] = False
    result["decision"] = DECISION_REJECT
    result["policy_result"] = POLICY_REJECT
    result["policy_reason"] = "operation state policy rejected"
    result["operation_policy_result"] = POLICY_REJECT
    result["operation_policy_reason"] = operation_reason
    result["blocked_reasons"] = [operation_reason]
    return result


def apply_routine_active_policy(
    policy_preview: dict[str, Any],
    routine_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply injected routine-active policy to a Policy Preview."""
    preview_error = _validate_policy_preview(policy_preview)
    if preview_error:
        return _reject(preview_error, policy_preview)
    if not isinstance(routine_state, dict):
        return _reject("routine_state must be dict", policy_preview)

    missing = _missing_fields(routine_state, ("enabled", "status"))
    if missing:
        return _reject("missing required routine_state fields: " + ", ".join(missing), policy_preview)

    result = _copy_policy_preview(policy_preview)
    result["routine_policy"] = ROUTINE_POLICY_ACTIVE
    enabled = routine_state.get("enabled") is True
    status = _status(routine_state)

    if not enabled:
        routine_reason = "routine_state.enabled is not true"
    elif status == ACTIVE_STATUS:
        result["routine_policy_result"] = POLICY_PASS
        result["routine_policy_reason"] = "routine active policy passed"
        result["blocked_reasons"] = []
        return result
    elif status in REJECT_ROUTINE_STATUSES:
        routine_reason = f"routine_state.status is {status}"
    else:
        routine_reason = "routine_state.status is invalid"

    result["ok"] = False
    result["decision"] = DECISION_REJECT
    result["policy_result"] = POLICY_REJECT
    result["policy_reason"] = "routine active policy rejected"
    result["routine_policy_result"] = POLICY_REJECT
    result["routine_policy_reason"] = routine_reason
    result["blocked_reasons"] = [routine_reason]
    return result


def apply_stock_active_policy(
    policy_preview: dict[str, Any],
    stock_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply injected stock-active policy to a Policy Preview."""
    preview_error = _validate_policy_preview(policy_preview)
    if preview_error:
        return _reject(preview_error, policy_preview)
    if not isinstance(stock_state, dict):
        return _reject("stock_state must be dict", policy_preview)

    missing = _missing_fields(stock_state, ("enabled", "status"))
    if missing:
        return _reject("missing required stock_state fields: " + ", ".join(missing), policy_preview)

    result = _copy_policy_preview(policy_preview)
    result["stock_policy"] = STOCK_POLICY_ACTIVE
    enabled = stock_state.get("enabled") is True
    status = _status(stock_state)

    if not enabled:
        stock_reason = "stock_state.enabled is not true"
    elif status == ACTIVE_STATUS:
        result["stock_policy_result"] = POLICY_PASS
        result["stock_policy_reason"] = "stock active policy passed"
        result["blocked_reasons"] = []
        return result
    elif status in REJECT_STOCK_STATUSES:
        stock_reason = f"stock_state.status is {status}"
    else:
        stock_reason = "stock_state.status is invalid"

    result["ok"] = False
    result["decision"] = DECISION_REJECT
    result["policy_result"] = POLICY_REJECT
    result["policy_reason"] = "stock active policy rejected"
    result["stock_policy_result"] = POLICY_REJECT
    result["stock_policy_reason"] = stock_reason
    result["blocked_reasons"] = [stock_reason]
    return result


def apply_emergency_detail_policy(
    policy_preview: dict[str, Any],
    emergency_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply injected emergency detail policy to a Policy Preview."""
    preview_error = _validate_policy_preview(policy_preview)
    if preview_error:
        return _reject(preview_error, policy_preview)
    if not isinstance(emergency_state, dict):
        return _reject("emergency_state must be dict", policy_preview)

    missing = _missing_fields(emergency_state, ("emergency_stop", "force_stop", "safety_lock"))
    if missing:
        return _reject("missing required emergency_state fields: " + ", ".join(missing), policy_preview)

    result = _copy_policy_preview(policy_preview)
    result["emergency_policy"] = EMERGENCY_POLICY_DETAIL
    for field in ("emergency_stop", "force_stop", "safety_lock"):
        if emergency_state.get(field) is True:
            emergency_reason = f"emergency_state.{field} is true"
            result["ok"] = False
            result["decision"] = DECISION_REJECT
            result["policy_result"] = POLICY_REJECT
            result["policy_reason"] = "emergency detail policy rejected"
            result["emergency_policy_result"] = POLICY_REJECT
            result["emergency_policy_reason"] = emergency_reason
            result["blocked_reasons"] = [emergency_reason]
            return result

    result["emergency_policy_result"] = POLICY_PASS
    result["emergency_policy_reason"] = "emergency detail policy passed"
    result["blocked_reasons"] = []
    return result


def apply_budget_policy(
    policy_preview: dict[str, Any],
    budget_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply injected budget policy to a Policy Preview."""
    preview_error = _validate_policy_preview(policy_preview)
    if preview_error:
        return _reject(preview_error, policy_preview)
    if not isinstance(budget_state, dict):
        return _reject("budget_state must be dict", policy_preview)

    missing = _missing_fields(budget_state, ("enabled", "available_budget", "required_budget"))
    if missing:
        return _reject("missing required budget_state fields: " + ", ".join(missing), policy_preview)

    result = _copy_policy_preview(policy_preview)
    result["budget_policy"] = BUDGET_POLICY
    available = _safe_float(budget_state.get("available_budget"))
    required = _safe_float(budget_state.get("required_budget"))

    if budget_state.get("enabled") is not True:
        budget_reason = "budget_state.enabled is not true"
    elif available is None or required is None:
        budget_reason = "budget_state budget values are invalid"
    elif available >= required:
        result["budget_policy_result"] = POLICY_PASS
        result["budget_policy_reason"] = "budget policy passed"
        result["blocked_reasons"] = []
        return result
    else:
        budget_reason = "budget_state.available_budget is less than required_budget"

    result["ok"] = False
    result["decision"] = DECISION_REJECT
    result["policy_result"] = POLICY_REJECT
    result["policy_reason"] = "budget policy rejected"
    result["budget_policy_result"] = POLICY_REJECT
    result["budget_policy_reason"] = budget_reason
    result["blocked_reasons"] = [budget_reason]
    return result


def apply_duplicate_signal_policy(
    policy_preview: dict[str, Any],
    signal_history: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply injected duplicate-signal policy to a Policy Preview."""
    preview_error = _validate_policy_preview(policy_preview)
    if preview_error:
        return _reject(preview_error, policy_preview)

    result = _copy_policy_preview(policy_preview)
    result["duplicate_policy"] = DUPLICATE_POLICY_SIGNAL
    current_signal = policy_preview.get("signal")
    if current_signal is None:
        result["duplicate_policy_result"] = POLICY_IGNORE
        result["duplicate_policy_reason"] = "current signal is None; duplicate policy ignored"
        result["blocked_reasons"] = []
        return result

    if not isinstance(signal_history, dict):
        return _reject("signal_history must be dict", policy_preview)

    missing = _missing_fields(signal_history, ("last_signal", "symbol", "routine_id", "signal"))
    if missing:
        return _reject("missing required signal_history fields: " + ", ".join(missing), policy_preview)

    previous_signal = str(signal_history.get("last_signal") or "").strip().upper()
    history_signal = str(signal_history.get("signal") or "").strip().upper()
    same_signal = previous_signal == current_signal and history_signal == current_signal
    same_symbol = bool(str(signal_history.get("symbol") or "").strip())
    same_routine = bool(str(signal_history.get("routine_id") or "").strip())

    if same_symbol and same_routine and same_signal:
        duplicate_reason = "duplicate signal for same symbol, routine_id, and signal"
        result["ok"] = False
        result["decision"] = DECISION_REJECT
        result["policy_result"] = POLICY_REJECT
        result["policy_reason"] = "duplicate signal policy rejected"
        result["duplicate_policy_result"] = POLICY_REJECT
        result["duplicate_policy_reason"] = duplicate_reason
        result["blocked_reasons"] = [duplicate_reason]
        return result

    result["duplicate_policy_result"] = POLICY_PASS
    result["duplicate_policy_reason"] = "duplicate signal policy passed"
    result["blocked_reasons"] = []
    return result


def apply_cooldown_policy(
    policy_preview: dict[str, Any],
    cooldown_state: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    datetime_provider: Any = None,
) -> dict[str, Any]:
    """Apply injected cooldown policy to a Policy Preview."""
    preview_error = _validate_policy_preview(policy_preview)
    if preview_error:
        return _reject(preview_error, policy_preview)
    if not isinstance(cooldown_state, dict):
        return _reject("cooldown_state must be dict", policy_preview)

    missing = _missing_fields(cooldown_state, ("enabled", "last_signal_time", "cooldown_seconds"))
    if missing:
        return _reject("missing required cooldown_state fields: " + ", ".join(missing), policy_preview)

    result = _copy_policy_preview(policy_preview)
    result["cooldown_policy"] = COOLDOWN_POLICY
    if cooldown_state.get("enabled") is not True:
        result["cooldown_policy_result"] = POLICY_PASS
        result["cooldown_policy_reason"] = "cooldown policy disabled"
        result["blocked_reasons"] = []
        return result

    last_signal_time = _parse_datetime(cooldown_state.get("last_signal_time"))
    if last_signal_time is None:
        result["cooldown_policy_result"] = POLICY_PASS
        result["cooldown_policy_reason"] = "last_signal_time is absent; cooldown policy passed"
        result["blocked_reasons"] = []
        return result

    now_dt = _now_from_inputs(now, datetime_provider)
    if now_dt is None:
        return _reject("now or datetime_provider is required", policy_preview)

    cooldown_seconds = _safe_int(cooldown_state.get("cooldown_seconds"))
    if cooldown_seconds is None:
        return _reject("cooldown_state.cooldown_seconds is invalid", policy_preview)

    elapsed = (now_dt - last_signal_time).total_seconds()
    if elapsed >= cooldown_seconds:
        result["cooldown_policy_result"] = POLICY_PASS
        result["cooldown_policy_reason"] = "cooldown elapsed"
        result["blocked_reasons"] = []
        return result

    cooldown_reason = "cooldown has not elapsed"
    result["ok"] = False
    result["decision"] = DECISION_REJECT
    result["policy_result"] = POLICY_REJECT
    result["policy_reason"] = "cooldown policy rejected"
    result["cooldown_policy_result"] = POLICY_REJECT
    result["cooldown_policy_reason"] = cooldown_reason
    result["blocked_reasons"] = [cooldown_reason]
    return result


def apply_delay_policy(
    policy_preview: dict[str, Any],
    delay_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply injected delay policy to a Policy Preview."""
    preview_error = _validate_policy_preview(policy_preview)
    if preview_error:
        return _reject(preview_error, policy_preview)
    if not isinstance(delay_state, dict):
        return _reject("delay_state must be dict", policy_preview)

    missing = _missing_fields(delay_state, ("enabled", "remaining_delay_bar"))
    if missing:
        return _reject("missing required delay_state fields: " + ", ".join(missing), policy_preview)

    result = _copy_policy_preview(policy_preview)
    result["delay_policy"] = DELAY_POLICY
    remaining = _safe_int(delay_state.get("remaining_delay_bar"))
    if delay_state.get("enabled") is not True:
        result["delay_policy_result"] = POLICY_PASS
        result["delay_policy_reason"] = "delay policy disabled"
        result["blocked_reasons"] = []
        return result
    if remaining is None:
        return _reject("delay_state.remaining_delay_bar is invalid", policy_preview)
    if remaining <= 0:
        result["delay_policy_result"] = POLICY_PASS
        result["delay_policy_reason"] = "delay policy passed"
        result["blocked_reasons"] = []
        return result

    delay_reason = "delay_state.remaining_delay_bar is greater than 0"
    result["ok"] = False
    result["decision"] = DECISION_REJECT
    result["policy_result"] = POLICY_REJECT
    result["policy_reason"] = "delay policy rejected"
    result["delay_policy_result"] = POLICY_REJECT
    result["delay_policy_reason"] = delay_reason
    result["blocked_reasons"] = [delay_reason]
    return result


def apply_all_signal_policies(
    policy_preview: dict[str, Any],
    *,
    market_snapshot: dict[str, Any],
    operation_state: dict[str, Any],
    routine_state: dict[str, Any],
    stock_state: dict[str, Any],
    emergency_state: dict[str, Any],
    budget_state: dict[str, Any],
    signal_history: dict[str, Any],
    cooldown_state: dict[str, Any],
    delay_state: dict[str, Any],
    now: datetime | None = None,
    datetime_provider: Any = None,
) -> dict[str, Any]:
    """Apply all signal decision policies in the fixed execution-gate order."""
    preview_error = _validate_policy_preview(policy_preview)
    if preview_error:
        return _with_orchestrator_result(
            _reject(preview_error, policy_preview),
            applied_policies=[],
            blocked_policy=POLICY_ORCHESTRATOR,
        )

    applied: list[str] = []
    current = policy_preview

    steps = (
        (
            ORCHESTRATED_POLICY_STEPS[0],
            lambda value: apply_time_policy(
                value,
                market_snapshot,
                now=now,
                datetime_provider=datetime_provider,
            ),
        ),
        (ORCHESTRATED_POLICY_STEPS[1], lambda value: apply_operation_state_policy(value, operation_state)),
        (ORCHESTRATED_POLICY_STEPS[2], lambda value: apply_routine_active_policy(value, routine_state)),
        (ORCHESTRATED_POLICY_STEPS[3], lambda value: apply_stock_active_policy(value, stock_state)),
        (ORCHESTRATED_POLICY_STEPS[4], lambda value: apply_emergency_detail_policy(value, emergency_state)),
        (ORCHESTRATED_POLICY_STEPS[5], lambda value: apply_budget_policy(value, budget_state)),
        (ORCHESTRATED_POLICY_STEPS[6], lambda value: apply_duplicate_signal_policy(value, signal_history)),
        (
            ORCHESTRATED_POLICY_STEPS[7],
            lambda value: apply_cooldown_policy(
                value,
                cooldown_state,
                now=now,
                datetime_provider=datetime_provider,
            ),
        ),
        (ORCHESTRATED_POLICY_STEPS[8], lambda value: apply_delay_policy(value, delay_state)),
    )

    for policy_name, apply_policy in steps:
        current = apply_policy(current)
        applied.append(policy_name)
        if current.get("ok") is not True or current.get("policy_result") == POLICY_REJECT:
            return _with_orchestrator_result(
                current,
                applied_policies=applied,
                blocked_policy=policy_name,
            )

    return _with_orchestrator_result(
        current,
        applied_policies=applied,
        blocked_policy=None,
    )
