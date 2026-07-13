"""Preview-only SELL execution readiness evaluation.

This module classifies SELL common execution preview candidates without
creating OrderRequest objects, writing runtime/queue files, or selecting a
candidate priority.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

PREVIEW_TYPE = "SELL_EXECUTION_READINESS_PREVIEW"
SOURCE_PREVIEW_TYPE = "SELL_COMMON_EXECUTION_PREVIEW_ADAPTER"
EXCLUDED_ACTION_SOURCES = {"PENDING", "CANCEL_PENDING_ORDER"}
_EXPECTED_STAGES = {
    "execution_preview": "EXECUTION_PREVIEW",
    "final_guard": "FINAL_EXECUTION_GUARD",
    "lock_preview": "ORDER_LOCK_PREVIEW",
    "request_hash_preview": "REQUEST_HASH_PREVIEW",
    "execution_request_preview": "EXECUTION_REQUEST_PREVIEW",
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
)


def build_sell_execution_readiness_preview(
    common_execution_preview: dict[str, Any],
) -> dict[str, Any]:
    """Build a read-only readiness preview from common execution preview output."""
    result = _base_result(common_execution_preview)

    if not isinstance(common_execution_preview, dict):
        result["status"] = INVALID
        result["reasons"].append("common_execution_preview must be a dict")
        return _finish(result)

    result["common_execution_preview_snapshot"] = deepcopy(common_execution_preview)

    if common_execution_preview.get("preview_type") != SOURCE_PREVIEW_TYPE:
        result["status"] = INVALID
        result["reasons"].append("common_execution_preview preview_type is invalid")
        return _finish(result)

    if common_execution_preview.get("preview_only") is not True:
        result["status"] = INVALID
        result["reasons"].append("common_execution_preview preview_only must be True")
        return _finish(result)

    if _has_forbidden_safety_flag(common_execution_preview):
        result["status"] = INVALID
        result["reasons"].append("common_execution_preview safety flag violation")
        return _finish(result)

    if _clean_text(common_execution_preview.get("status")).upper() == INVALID:
        result["status"] = INVALID
        result["reasons"].append("common_execution_preview status is INVALID")
        return _finish(result)

    if isinstance(common_execution_preview.get("warnings"), list):
        result["warnings"].extend(deepcopy(common_execution_preview["warnings"]))

    candidate_results = common_execution_preview.get("candidate_results")
    if not isinstance(candidate_results, list):
        result["status"] = INVALID
        result["reasons"].append("candidate_results must be a list")
        return _finish(result)

    result["summary"]["candidate_count"] = len(candidate_results)
    result["blocked_candidates"] = (
        deepcopy(common_execution_preview.get("blocked_candidates"))
        if isinstance(common_execution_preview.get("blocked_candidates"), list)
        else []
    )

    if _clean_text(common_execution_preview.get("status")).upper() == BLOCKED:
        result["status"] = BLOCKED
        result["reasons"].append("common_execution_preview status is BLOCKED")
        result["summary"]["blocked_candidate_count"] = len(candidate_results)
        return _finish(result)

    has_invalid = False
    for index, candidate in enumerate(candidate_results):
        readiness = _classify_candidate(candidate, index)
        result["candidate_readiness"].append(readiness)

        if readiness["status"] == READY:
            result["summary"]["ready_candidate_count"] += 1
            result["ready_candidates"].append(deepcopy(readiness))
        elif readiness["status"] == INVALID:
            result["summary"]["invalid_candidate_count"] += 1
            has_invalid = True
            result["blocked_candidate_readiness"].append(deepcopy(readiness))
        else:
            result["summary"]["blocked_candidate_count"] += 1
            result["blocked_candidate_readiness"].append(deepcopy(readiness))

        stage_checks = readiness.get("stage_checks")
        if isinstance(stage_checks, dict):
            if stage_checks.get("final_guard") is True:
                result["summary"]["final_guard_pass_count"] += 1
            if stage_checks.get("lock_preview") is True:
                result["summary"]["lock_preview_confirmed_count"] += 1
            if stage_checks.get("request_hash_preview") is True:
                result["summary"]["request_hash_preview_confirmed_count"] += 1
            if stage_checks.get("execution_request_preview") is True:
                result["summary"]["execution_request_preview_confirmed_count"] += 1

    if has_invalid:
        result["status"] = INVALID
    elif result["summary"]["ready_candidate_count"] > 0:
        result["status"] = READY
    else:
        result["status"] = BLOCKED
        if not result["reasons"]:
            result["reasons"].append("no READY SELL execution readiness candidates")

    return _finish(result)


def _base_result(common_execution_preview: Any) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Preview",
        "routine_dependency": None,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order": False,
        "broker_api_called": False,
        "real_ready_state_changed": False,
        "order_request_created": False,
        "status": BLOCKED,
        "readiness_ready": False,
        "ready_candidates": [],
        "candidate_readiness": [],
        "blocked_candidate_readiness": [],
        "blocked_candidates": [],
        "summary": {
            "candidate_count": 0,
            "ready_candidate_count": 0,
            "blocked_candidate_count": 0,
            "invalid_candidate_count": 0,
            "final_guard_pass_count": 0,
            "lock_preview_confirmed_count": 0,
            "request_hash_preview_confirmed_count": 0,
            "execution_request_preview_confirmed_count": 0,
            "priority_selected": False,
            "auto_selected": False,
        },
        "warnings": [],
        "reasons": [],
        "common_execution_preview_snapshot": deepcopy(common_execution_preview)
        if isinstance(common_execution_preview, dict)
        else {},
    }


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    result["readiness_ready"] = result.get("status") == READY
    result["summary"]["priority_selected"] = False
    result["summary"]["auto_selected"] = False
    return result


def _classify_candidate(candidate: Any, index: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return _candidate_result(index, "UNKNOWN", INVALID, None, ["candidate must be a dict"])

    action_source = _clean_text(candidate.get("action_source")).upper() or "UNKNOWN"
    snapshot = deepcopy(candidate)
    reasons: list[str] = []
    warnings = deepcopy(candidate.get("warnings")) if isinstance(candidate.get("warnings"), list) else []
    stage_checks = {
        "execution_preview": False,
        "final_guard": False,
        "lock_preview": False,
        "request_hash_preview": False,
        "execution_request_preview": False,
    }

    if action_source in EXCLUDED_ACTION_SOURCES:
        reasons.append("PENDING cancel action requires a separate cancel execution path")
        return _candidate_result(index, action_source, BLOCKED, snapshot, reasons, warnings, stage_checks)

    if _has_forbidden_safety_flag(candidate):
        return _candidate_result(
            index,
            action_source,
            INVALID,
            snapshot,
            ["candidate safety flag violation"],
            warnings,
            stage_checks,
        )

    candidate_status = _clean_text(candidate.get("status")).upper()
    if candidate_status == INVALID:
        return _candidate_result(
            index,
            action_source,
            INVALID,
            snapshot,
            ["candidate status is INVALID"],
            warnings,
            stage_checks,
        )
    if candidate_status != READY:
        reasons.append("candidate status is not READY")

    candidate_snapshot = candidate.get("candidate_snapshot")
    if not isinstance(candidate_snapshot, dict):
        return _candidate_result(
            index,
            action_source,
            INVALID,
            snapshot,
            ["candidate_snapshot must be a dict"],
            warnings,
            stage_checks,
        )

    if _clean_text(candidate_snapshot.get("side")).upper() != "SELL":
        return _candidate_result(
            index,
            action_source,
            INVALID,
            snapshot,
            ["candidate_snapshot side must be SELL"],
            warnings,
            stage_checks,
        )

    order_type = _clean_text(candidate_snapshot.get("order_type")).upper()
    if order_type and order_type != "SELL":
        return _candidate_result(
            index,
            action_source,
            INVALID,
            snapshot,
            ["candidate_snapshot order_type must be SELL"],
            warnings,
            stage_checks,
        )

    for identity_key in ("id", "source_signal_id"):
        if not _clean_text(candidate_snapshot.get(identity_key)):
            reasons.append(f"candidate_snapshot {identity_key} is required")

    pipeline_result = candidate.get("pipeline_result")
    if not isinstance(pipeline_result, dict):
        return _candidate_result(
            index,
            action_source,
            INVALID,
            snapshot,
            ["pipeline_result must be a dict"],
            warnings,
            stage_checks,
        )
    if pipeline_result.get("ok") is not True:
        reasons.append(_pipeline_reason(pipeline_result))

    if _clean_text(candidate_snapshot.get("hoga")).upper() == "MARKET":
        reasons.append("MARKET candidates stay blocked until common price contract is reviewed")

    for key, label in (
        ("execution_preview", "execution_preview"),
        ("final_guard", "final_guard"),
        ("lock_preview", "lock_preview"),
        ("request_hash_preview", "request_hash_preview"),
        ("execution_request_preview", "execution_request_preview"),
    ):
        stage_result = candidate.get(key)
        if not isinstance(stage_result, dict):
            reasons.append(f"{label} is required")
            continue
        if not _stage_ok(label, stage_result):
            reasons.append(f"{label} is not ready")
        else:
            stage_checks[label] = True

    status = READY if not reasons else BLOCKED
    return _candidate_result(index, action_source, status, snapshot, reasons, warnings, stage_checks)


def _candidate_result(
    index: int,
    action_source: str,
    status: str,
    snapshot: dict[str, Any] | None,
    reasons: list[str],
    warnings: list[str] | None = None,
    stage_checks: dict[str, bool] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "readiness_ready": status == READY,
        "candidate_index": index,
        "action_source": action_source,
        "candidate_snapshot": deepcopy(snapshot) if isinstance(snapshot, dict) else snapshot,
        "priority_selected": False,
        "auto_selected": False,
        "stage_checks": deepcopy(stage_checks) if isinstance(stage_checks, dict) else {},
        "reasons": list(reasons),
        "warnings": list(warnings or []),
    }


def _stage_ok(label: str, stage_result: dict[str, Any]) -> bool:
    if stage_result.get("stage") != _EXPECTED_STAGES.get(label):
        return False
    if stage_result.get("ok") is not True:
        return False
    if stage_result.get("unresolved") is True:
        return False
    return True


def _pipeline_reason(pipeline_result: dict[str, Any]) -> str:
    return str(
        pipeline_result.get("blocked_reason")
        or pipeline_result.get("blocked_stage")
        or "pipeline_result is not ok"
    )


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(payload.get(flag) is True for flag in _SAFETY_FLAGS)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
