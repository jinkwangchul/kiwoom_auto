"""Preview-only bridge from SELL REAL_READY candidates to common execution preview."""

from __future__ import annotations

from copy import deepcopy
from numbers import Number
from typing import Any

from execution_pipeline_controller import run_execution_preview_pipeline


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

PREVIEW_TYPE = "SELL_COMMON_EXECUTION_PREVIEW_ADAPTER"
SOURCE_PREVIEW_TYPE = "SELL_REAL_READY_ADAPTER_PREVIEW"
SUPPORTED_ACTION_SOURCES = {"METHOD", "COMPLETION"}
EXCLUDED_ACTION_SOURCES = {"PENDING", "CANCEL_PENDING_ORDER"}

_SAFETY_FLAGS = (
    "execution_connected",
    "runtime_write",
    "queue_write",
    "file_write",
    "send_order",
    "real_ready_state_changed",
)


def build_sell_common_execution_preview(
    adapter_preview: dict[str, Any],
    guard_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run common execution preview for eligible SELL LIMIT candidates only."""
    result = _base_result(adapter_preview, guard_context)

    if not isinstance(adapter_preview, dict):
        result["status"] = INVALID
        result["reasons"].append("adapter_preview must be a dict")
        return _finish(result)

    result["adapter_preview_snapshot"] = deepcopy(adapter_preview)

    if adapter_preview.get("preview_type") != SOURCE_PREVIEW_TYPE:
        result["status"] = INVALID
        result["reasons"].append("adapter_preview preview_type is invalid")
        return _finish(result)

    if _has_forbidden_safety_flag(adapter_preview):
        result["status"] = INVALID
        result["reasons"].append("adapter_preview safety flag violation")
        return _finish(result)

    candidates = adapter_preview.get("order_candidates")
    if not isinstance(candidates, list):
        result["status"] = INVALID
        result["reasons"].append("order_candidates must be a list")
        return _finish(result)

    if isinstance(adapter_preview.get("warnings"), list):
        result["warnings"].extend(deepcopy(adapter_preview["warnings"]))

    result["summary"]["candidate_count"] = len(candidates)

    guard_block_reason = _guard_block_reason(guard_context)
    if guard_block_reason:
        result["status"] = BLOCKED
        result["reasons"].append(guard_block_reason)
        result["summary"]["blocked_candidate_count"] = len(candidates)
        return _finish(result)

    has_invalid = False
    for index, candidate in enumerate(candidates):
        normalized = _normalize_candidate(candidate, index)
        if normalized["status"] == INVALID:
            has_invalid = True
            result["blocked_candidates"].append(normalized)
            result["summary"]["invalid_candidate_count"] += 1
            continue

        if normalized["status"] == BLOCKED:
            result["blocked_candidates"].append(normalized)
            result["summary"]["blocked_candidate_count"] += 1
            continue

        candidate_payload = normalized["candidate_snapshot"]
        pipeline_result = run_execution_preview_pipeline(
            deepcopy(candidate_payload),
            deepcopy(guard_context),
        )
        result["pipeline_preview_called"] = True
        result["summary"]["pipeline_called_count"] += 1

        candidate_result = _candidate_result(index, candidate_payload, pipeline_result)
        result["candidate_results"].append(candidate_result)

        if candidate_result["status"] == READY:
            result["summary"]["ready_candidate_count"] += 1
        else:
            result["blocked_candidates"].append(
                _blocked_candidate(
                    index,
                    candidate_payload,
                    "common execution preview pipeline blocked candidate",
                    pipeline_result=pipeline_result,
                )
            )
            result["summary"]["blocked_candidate_count"] += 1

    if has_invalid:
        result["status"] = INVALID
    elif result["summary"]["ready_candidate_count"] > 0:
        result["status"] = READY
    else:
        result["status"] = BLOCKED
        if not result["reasons"]:
            result["reasons"].append("no eligible SELL common execution preview candidates")

    return _finish(result)


def _base_result(
    adapter_preview: Any,
    guard_context: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "preview_only": True,
        "common_execution_ready": False,
        "execution_connected": False,
        "pipeline_preview_called": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "status": BLOCKED,
        "candidate_results": [],
        "blocked_candidates": [],
        "summary": {
            "candidate_count": 0,
            "pipeline_called_count": 0,
            "ready_candidate_count": 0,
            "blocked_candidate_count": 0,
            "invalid_candidate_count": 0,
            "priority_selected": False,
            "auto_selected": False,
        },
        "warnings": [],
        "reasons": [],
        "adapter_preview_snapshot": deepcopy(adapter_preview) if isinstance(adapter_preview, dict) else {},
        "guard_context_snapshot": deepcopy(guard_context) if isinstance(guard_context, dict) else {},
    }


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["common_execution_ready"] = result.get("status") == READY
    return result


def _guard_block_reason(guard_context: Any) -> str | None:
    if not isinstance(guard_context, dict):
        return "guard_context must be a dict"
    if guard_context.get("operator_confirmed") is not True:
        return "guard_context.operator_confirmed must be True"
    if guard_context.get("real_trade_enabled") is not True:
        return "guard_context.real_trade_enabled must be True"
    return None


def _normalize_candidate(candidate: Any, index: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return _invalid_candidate(index, "candidate must be a dict")

    action_source = _clean_text(candidate.get("action_source")).upper()
    if action_source in EXCLUDED_ACTION_SOURCES:
        return _blocked_candidate(
            index,
            candidate,
            "PENDING cancel action requires a separate cancel execution path",
        )

    if action_source not in SUPPORTED_ACTION_SOURCES:
        return _blocked_candidate(index, candidate, "unsupported action_source")

    if _has_forbidden_safety_flag(candidate):
        return _invalid_candidate(index, "candidate safety flag violation", candidate)

    reasons: list[str] = []
    invalid_reasons: list[str] = []

    if _clean_text(candidate.get("status")).upper() != "REAL_READY":
        reasons.append("candidate status must be REAL_READY")
    if candidate.get("execution_enabled") is not True:
        reasons.append("candidate execution_enabled must be True")
    if _clean_text(candidate.get("side")).upper() != "SELL":
        invalid_reasons.append("candidate side must be SELL")
    if _clean_text(candidate.get("order_type")).upper() != "SELL":
        invalid_reasons.append("candidate order_type must be SELL")

    hoga = _clean_text(candidate.get("hoga")).upper()
    if hoga == "MARKET":
        reasons.append("MARKET candidates stay blocked until common price contract is reviewed")
    elif hoga != "LIMIT":
        reasons.append("candidate hoga must be LIMIT")

    if hoga == "LIMIT" and not _positive_number(candidate.get("price")):
        reasons.append("LIMIT price must be positive")

    for key, reason in (
        ("id", "candidate id is required"),
        ("source_signal_id", "source_signal_id is required"),
        ("code", "code is required"),
    ):
        if not _clean_text(candidate.get(key)):
            reasons.append(reason)

    if not _positive_number(candidate.get("quantity")):
        reasons.append("quantity must be positive")

    if not isinstance(candidate.get("order_intent"), dict):
        reasons.append("order_intent must be a dict")

    if invalid_reasons:
        return _invalid_candidate(index, "; ".join(invalid_reasons), candidate)
    if reasons:
        return _blocked_candidate(index, candidate, "; ".join(reasons))

    return {
        "status": READY,
        "candidate_index": index,
        "action_source": action_source,
        "candidate_snapshot": deepcopy(candidate),
        "reasons": [],
        "warnings": deepcopy(candidate.get("warnings")) if isinstance(candidate.get("warnings"), list) else [],
    }


def _candidate_result(
    index: int,
    candidate: dict[str, Any],
    pipeline_result: dict[str, Any],
) -> dict[str, Any]:
    pipeline = pipeline_result.get("pipeline") if isinstance(pipeline_result, dict) else {}
    if not isinstance(pipeline, dict):
        pipeline = {}

    ok = bool(pipeline_result.get("ok")) if isinstance(pipeline_result, dict) else False
    return {
        "status": READY if ok else BLOCKED,
        "candidate_index": index,
        "action_source": _clean_text(candidate.get("action_source")).upper(),
        "candidate_snapshot": deepcopy(candidate),
        "pipeline_result": deepcopy(pipeline_result),
        "execution_preview": deepcopy(pipeline.get("execution_preview")),
        "final_guard": deepcopy(pipeline.get("final_guard")),
        "lock_preview": deepcopy(pipeline.get("lock_preview")),
        "request_hash_preview": deepcopy(pipeline.get("request_hash_preview")),
        "execution_request_preview": deepcopy(pipeline.get("execution_request_preview")),
        "warnings": deepcopy(pipeline_result.get("warnings")) if isinstance(pipeline_result.get("warnings"), list) else [],
        "reasons": [] if ok else [_pipeline_block_reason(pipeline_result)],
    }


def _pipeline_block_reason(pipeline_result: Any) -> str:
    if not isinstance(pipeline_result, dict):
        return "common execution preview pipeline result is invalid"
    return str(
        pipeline_result.get("blocked_reason")
        or pipeline_result.get("blocked_stage")
        or "common execution preview pipeline blocked"
    )


def _blocked_candidate(
    index: int,
    candidate: dict[str, Any],
    reason: str,
    *,
    pipeline_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": BLOCKED,
        "candidate_index": index,
        "action_source": _clean_text(candidate.get("action_source")).upper(),
        "candidate_snapshot": deepcopy(candidate),
        "pipeline_result": deepcopy(pipeline_result) if pipeline_result is not None else None,
        "reasons": [reason],
        "warnings": deepcopy(candidate.get("warnings")) if isinstance(candidate.get("warnings"), list) else [],
    }


def _invalid_candidate(
    index: int,
    reason: str,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": INVALID,
        "candidate_index": index,
        "action_source": _clean_text(candidate.get("action_source")).upper() if isinstance(candidate, dict) else "UNKNOWN",
        "candidate_snapshot": deepcopy(candidate) if isinstance(candidate, dict) else None,
        "pipeline_result": None,
        "reasons": [reason],
        "warnings": deepcopy(candidate.get("warnings")) if isinstance(candidate, dict) and isinstance(candidate.get("warnings"), list) else [],
    }


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(payload.get(flag) is True for flag in _SAFETY_FLAGS)


def _positive_number(value: Any) -> bool:
    return isinstance(value, Number) and not isinstance(value, bool) and value > 0


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
