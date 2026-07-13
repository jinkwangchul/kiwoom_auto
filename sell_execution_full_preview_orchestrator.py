"""Preview-only SELL execution full preview orchestrator.

This module runs the four SELL execution preview adapters in order and
aggregates their results. It does not choose candidate priority, mutate runtime
state, commit queue records, or send orders.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from sell_common_execution_preview_adapter import build_sell_common_execution_preview
from sell_execution_queue_preview import build_sell_execution_queue_preview
from sell_execution_readiness_preview import build_sell_execution_readiness_preview
from sell_signal_gate_preview import build_sell_signal_gate_preview


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

PREVIEW_TYPE = "SELL_EXECUTION_FULL_PREVIEW"
SOURCE_PREVIEW_TYPE = "SELL_REAL_READY_ADAPTER_PREVIEW"
OWNERSHIP = "MASTER_ENGINE"
DOMAIN = "Execution / Full Preview Orchestration"
ROUTINE_DEPENDENCY = None

_STEP_COMMON = "CommonExecutionPreview"
_STEP_READINESS = "ExecutionReadinessPreview"
_STEP_SIGNAL_GATE = "SignalGatePreview"
_STEP_QUEUE = "ExecutionQueuePreview"

_READY_FLAG_BY_STEP = {
    _STEP_COMMON: "common_execution_ready",
    _STEP_READINESS: "readiness_ready",
    _STEP_SIGNAL_GATE: "signal_gate_ready",
    _STEP_QUEUE: "execution_queue_ready",
}

_SAFETY_FLAGS = (
    "execution_connected",
    "runtime_write",
    "queue_write",
    "file_write",
    "send_order",
    "broker_api_called",
    "real_ready_state_changed",
    "order_request_created",
    "queue_committed",
)


def build_sell_execution_full_preview(
    adapter_preview: dict[str, Any],
    *,
    guard_context: dict[str, Any] | None = None,
    existing_orders: Any = None,
) -> dict[str, Any]:
    """Run the full SELL execution preview chain without side effects."""
    result = _base_result(adapter_preview, guard_context, existing_orders)

    validation_reason = _input_validation_reason(adapter_preview, guard_context, existing_orders)
    if validation_reason:
        result["status"] = INVALID
        result["reasons"].append(validation_reason)
        return _finish(result)

    common_preview = _call_stage(
        build_sell_common_execution_preview,
        _STEP_COMMON,
        result,
        deepcopy(adapter_preview),
        guard_context=deepcopy(guard_context),
    )
    result["common_execution_preview"] = common_preview
    if not _advance_or_stop(result, _STEP_COMMON, common_preview):
        return _finish(result)

    readiness_preview = _call_stage(
        build_sell_execution_readiness_preview,
        _STEP_READINESS,
        result,
        deepcopy(common_preview),
    )
    result["execution_readiness_preview"] = readiness_preview
    if not _advance_or_stop(result, _STEP_READINESS, readiness_preview):
        return _finish(result)

    signal_gate_preview = _call_stage(
        build_sell_signal_gate_preview,
        _STEP_SIGNAL_GATE,
        result,
        deepcopy(readiness_preview),
    )
    result["signal_gate_preview"] = signal_gate_preview
    if not _advance_or_stop(result, _STEP_SIGNAL_GATE, signal_gate_preview):
        return _finish(result)

    queue_preview = _call_stage(
        build_sell_execution_queue_preview,
        _STEP_QUEUE,
        result,
        deepcopy(signal_gate_preview),
        guard_context=deepcopy(guard_context),
        existing_orders=deepcopy(existing_orders),
    )
    result["execution_queue_preview"] = queue_preview
    _advance_or_stop(result, _STEP_QUEUE, queue_preview)
    if result["status"] == READY:
        result["completed"] = True
    return _finish(result)


run_sell_execution_full_preview = build_sell_execution_full_preview


def _base_result(
    adapter_preview: Any,
    guard_context: dict[str, Any] | None,
    existing_orders: Any,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "ownership": OWNERSHIP,
        "domain": DOMAIN,
        "routine_dependency": ROUTINE_DEPENDENCY,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order": False,
        "broker_api_called": False,
        "real_ready_state_changed": False,
        "order_request_created": False,
        "queue_committed": False,
        "actual_order_sent": False,
        "status": BLOCKED,
        "completed": False,
        "adapter_preview_snapshot": deepcopy(adapter_preview) if isinstance(adapter_preview, dict) else {},
        "guard_context_snapshot": deepcopy(guard_context) if isinstance(guard_context, dict) else {},
        "existing_orders_snapshot": deepcopy(existing_orders) if isinstance(existing_orders, list) else existing_orders,
        "common_execution_preview": {},
        "execution_readiness_preview": {},
        "signal_gate_preview": {},
        "execution_queue_preview": {},
        "preview_steps": {
            _STEP_COMMON: "SKIP",
            _STEP_READINESS: "SKIP",
            _STEP_SIGNAL_GATE: "SKIP",
            _STEP_QUEUE: "SKIP",
        },
        "summary": {
            "common_status": None,
            "readiness_status": None,
            "signal_gate_status": None,
            "execution_queue_status": None,
            "ready_candidate_count": 0,
            "blocked_candidate_count": 0,
            "invalid_candidate_count": 0,
            "opened_gate_count": 0,
            "queue_ready_count": 0,
            "order_queued_preview_count": 0,
            "priority_selected": False,
            "auto_selected": False,
            "queue_committed": False,
        },
        "warnings": [],
        "reasons": [],
    }


def _input_validation_reason(
    adapter_preview: Any,
    guard_context: Any,
    existing_orders: Any,
) -> str | None:
    if not isinstance(adapter_preview, dict):
        return "adapter_preview must be a dict"
    if adapter_preview.get("preview_type") != SOURCE_PREVIEW_TYPE:
        return "adapter_preview preview_type is invalid"
    if adapter_preview.get("preview_only") is not True:
        return "adapter_preview preview_only must be True"
    if _has_forbidden_safety_flag(adapter_preview):
        return "adapter_preview safety flag violation"
    if guard_context is not None and not isinstance(guard_context, dict):
        return "guard_context must be a dict or None"
    if existing_orders is not None and not isinstance(existing_orders, list):
        return "existing_orders must be a list or None"
    return None


def _call_stage(
    builder: Callable[..., dict[str, Any]],
    step: str,
    result: dict[str, Any],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        stage_result = builder(*args, **kwargs)
    except Exception as exc:  # pragma: no cover - defensive boundary
        result["status"] = INVALID
        result["preview_steps"][step] = "FAIL"
        result["reasons"].append(f"{_failure_prefix(step)}: {exc}")
        return {}
    if not isinstance(stage_result, dict):
        result["status"] = INVALID
        result["preview_steps"][step] = "FAIL"
        result["reasons"].append(f"{_failure_prefix(step)}: result must be a dict")
        return {}
    return stage_result


def _advance_or_stop(result: dict[str, Any], step: str, stage_result: dict[str, Any]) -> bool:
    if not stage_result:
        return False

    status = _status(stage_result.get("status"))
    if _has_forbidden_safety_flag(stage_result):
        status = INVALID
        stage_result = {**stage_result, "status": INVALID}
        result["reasons"].append(f"{step} safety flag violation")
    elif status == READY and stage_result.get(_READY_FLAG_BY_STEP[step]) is not True:
        status = INVALID
        stage_result = {**stage_result, "status": INVALID}
        result["reasons"].append(f"{step} ready flag is not True")

    _record_stage_status(result, step, status)
    _merge_messages(result, stage_result)
    _merge_summary(result, step, stage_result)

    if status == READY:
        result["preview_steps"][step] = "PASS"
        result["status"] = READY
        return True

    result["preview_steps"][step] = "FAIL"
    result["status"] = INVALID if status == INVALID else BLOCKED
    result["completed"] = False
    return False


def _record_stage_status(result: dict[str, Any], step: str, status: str) -> None:
    key_by_step = {
        _STEP_COMMON: "common_status",
        _STEP_READINESS: "readiness_status",
        _STEP_SIGNAL_GATE: "signal_gate_status",
        _STEP_QUEUE: "execution_queue_status",
    }
    result["summary"][key_by_step[step]] = status


def _merge_summary(result: dict[str, Any], step: str, stage_result: dict[str, Any]) -> None:
    summary = stage_result.get("summary")
    if not isinstance(summary, dict):
        return

    if step in {_STEP_COMMON, _STEP_READINESS}:
        _set_int(result, "ready_candidate_count", summary.get("ready_candidate_count"))
        _set_int(result, "blocked_candidate_count", summary.get("blocked_candidate_count"))
        _set_int(result, "invalid_candidate_count", summary.get("invalid_candidate_count"))
    elif step == _STEP_SIGNAL_GATE:
        _set_int(result, "opened_gate_count", summary.get("opened_gate_count"))
        _set_int(result, "blocked_candidate_count", summary.get("blocked_gate_count"))
        _set_int(result, "invalid_candidate_count", summary.get("invalid_candidate_count"))
    elif step == _STEP_QUEUE:
        _set_int(result, "queue_ready_count", summary.get("queue_ready_count"))
        _set_int(result, "order_queued_preview_count", summary.get("order_queued_preview_count"))
        _set_int(result, "blocked_candidate_count", summary.get("blocked_count"))
        _set_int(result, "invalid_candidate_count", summary.get("invalid_count"))


def _set_int(result: dict[str, Any], key: str, value: Any) -> None:
    if isinstance(value, int) and not isinstance(value, bool):
        result["summary"][key] = value


def _merge_messages(result: dict[str, Any], stage_result: dict[str, Any]) -> None:
    for target, source_key in (("warnings", "warnings"), ("reasons", "reasons")):
        source = stage_result.get(source_key)
        if not isinstance(source, list):
            continue
        for item in source:
            text = str(item)
            if text not in result[target]:
                result[target].append(text)


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["completed"] = result.get("status") == READY and result.get("completed") is True
    result["summary"]["priority_selected"] = False
    result["summary"]["auto_selected"] = False
    result["summary"]["queue_committed"] = False
    result["execution_connected"] = False
    result["runtime_write"] = False
    result["queue_write"] = False
    result["file_write"] = False
    result["send_order"] = False
    result["broker_api_called"] = False
    result["real_ready_state_changed"] = False
    result["order_request_created"] = False
    result["queue_committed"] = False
    result["actual_order_sent"] = False
    return result


def _failure_prefix(step: str) -> str:
    return {
        _STEP_COMMON: "COMMON_PREVIEW_FAILED",
        _STEP_READINESS: "READINESS_PREVIEW_FAILED",
        _STEP_SIGNAL_GATE: "SIGNAL_GATE_PREVIEW_FAILED",
        _STEP_QUEUE: "EXECUTION_QUEUE_PREVIEW_FAILED",
    }[step]


def _status(value: Any) -> str:
    text = "" if value is None else str(value).strip().upper()
    if text in {READY, BLOCKED, INVALID}:
        return text
    return INVALID


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(payload.get(flag) is True for flag in _SAFETY_FLAGS)
