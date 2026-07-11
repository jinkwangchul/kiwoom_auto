# -*- coding: utf-8 -*-
"""Preview-only BUY runtime state projection.

This module converts a READY BUY order candidate draft preview into a runtime
state change preview. It never writes runtime files, commits state, queues an
order, calls lifecycle commit, SendOrder, Broker, Chejan, or GUI code.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


PREVIEW_TYPE = "BUY_EXECUTION_RUNTIME_PROJECTION_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
TARGET = "buy_execution_state"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    if number is None:
        return None
    return int(number)


def _num(value: Any, default: float = 0.0) -> float:
    number = _safe_float(value)
    return default if number is None else number


def _result(
    *,
    status: str,
    runtime_projection: dict[str, Any] | None = None,
    runtime_patch_preview: dict[str, Any] | None = None,
    before_state: dict[str, Any] | None = None,
    after_state_candidate: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "lifecycle_commit_called": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_connected": False,
        "gui_updated": False,
        "runtime_projection": deepcopy(runtime_projection) if isinstance(runtime_projection, dict) else None,
        "runtime_patch_preview": deepcopy(runtime_patch_preview) if isinstance(runtime_patch_preview, dict) else None,
        "before_state": deepcopy(before_state) if isinstance(before_state, dict) else None,
        "after_state_candidate": deepcopy(after_state_candidate) if isinstance(after_state_candidate, dict) else None,
        "execution_snapshot": deepcopy(execution_snapshot or {}),
        "evidence": deepcopy(evidence or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "issues": list(issues or []),
    }


def _blocked(status: str, issue: str, candidate_preview: dict[str, Any], before_state: dict[str, Any]) -> dict[str, Any]:
    return _result(
        status=status,
        before_state=before_state,
        execution_snapshot=_as_dict(_as_dict(candidate_preview.get("execution_snapshot"))),
        evidence=_as_dict(candidate_preview.get("evidence")),
        diagnostics=[{"stage": "projection_input", "ok": False, "reason": issue}],
        issues=[issue],
    )


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key, value in after.items():
        if before.get(key) != value:
            changes[key] = deepcopy(value)
    return changes


def build_buy_runtime_projection_preview(
    *,
    buy_candidate_preview: Any,
    runtime_state_snapshot: Any,
    execution_policy_snapshot: Any = None,
    projection_context: Any = None,
) -> dict[str, Any]:
    """Build a preview-only runtime state projection from a BUY candidate draft."""
    candidate_preview = deepcopy(_as_dict(buy_candidate_preview))
    before_state = deepcopy(_as_dict(runtime_state_snapshot))
    policy_snapshot = deepcopy(_as_dict(execution_policy_snapshot))
    context = deepcopy(_as_dict(projection_context))

    if not candidate_preview:
        return _blocked(STATUS_INVALID, "MALFORMED_CANDIDATE_PREVIEW", candidate_preview, before_state)
    candidate_status = str(candidate_preview.get("status") or "").strip().upper()
    if candidate_status == STATUS_BLOCKED:
        return _blocked(STATUS_BLOCKED, "CANDIDATE_PREVIEW_BLOCKED", candidate_preview, before_state)
    if candidate_status == STATUS_INVALID:
        return _blocked(STATUS_INVALID, "CANDIDATE_PREVIEW_INVALID", candidate_preview, before_state)
    if candidate_status != STATUS_READY:
        return _blocked(STATUS_INVALID, "CANDIDATE_PREVIEW_STATUS_NOT_READY", candidate_preview, before_state)

    draft = _as_dict(candidate_preview.get("order_candidate_draft"))
    if not draft:
        return _blocked(STATUS_INVALID, "CANDIDATE_DRAFT_MISSING", candidate_preview, before_state)

    candidate_id = str(draft.get("candidate_id") or "").strip()
    source_signal_id = draft.get("source_signal_id")
    execution_snapshot = _as_dict(draft.get("execution_snapshot"))
    if not candidate_id:
        return _blocked(STATUS_INVALID, "CANDIDATE_ID_MISSING", candidate_preview, before_state)
    if not execution_snapshot:
        return _blocked(STATUS_INVALID, "EXECUTION_SNAPSHOT_MISSING", candidate_preview, before_state)

    next_round = _safe_int(draft.get("next_buy_round"))
    budget = _safe_float(draft.get("budget"))
    previous_round = _safe_int(before_state.get("current_buy_round")) or 0
    before_executed = _safe_int(before_state.get("executed_buy_rounds")) or 0
    before_budget = _safe_float(before_state.get("cumulative_buy_budget")) or 0.0
    issues: list[str] = []
    if next_round is None or next_round <= 0:
        issues.append("INVALID_CANDIDATE_ROUND")
    if budget is None or budget <= 0:
        issues.append("INVALID_CANDIDATE_BUDGET")
    if _safe_int(before_state.get("executed_buy_rounds")) is None and "executed_buy_rounds" in before_state:
        issues.append("INVALID_RUNTIME_EXECUTED_ROUNDS")
    if _safe_float(before_state.get("cumulative_buy_budget")) is None and "cumulative_buy_budget" in before_state:
        issues.append("INVALID_RUNTIME_CUMULATIVE_BUDGET")
    if next_round is not None and next_round < previous_round:
        issues.append("CANDIDATE_ROUND_BEFORE_CURRENT_ROUND")

    policy_hash = str(execution_snapshot.get("policy_hash") or "").strip()
    approved_rule_hash = str(execution_snapshot.get("approved_rule_hash") or "").strip()
    if not policy_hash:
        issues.append("POLICY_HASH_MISSING")
    expected_policy_hash = str(policy_snapshot.get("policy_hash") or "").strip()
    if expected_policy_hash and policy_hash and expected_policy_hash != policy_hash:
        issues.append("POLICY_HASH_MISMATCH")

    runtime_state_hash_before = _stable_hash(before_state)
    expected_before_hash = (
        str(context.get("expected_runtime_state_hash") or "").strip()
        or str(policy_snapshot.get("runtime_state_hash") or "").strip()
        or str(execution_snapshot.get("runtime_state_hash") or "").strip()
    )
    if expected_before_hash and expected_before_hash != runtime_state_hash_before:
        issues.append("BEFORE_STATE_HASH_MISMATCH")

    if issues:
        return _result(
            status=STATUS_INVALID,
            before_state=before_state,
            execution_snapshot=execution_snapshot,
            evidence={
                "candidate_id": candidate_id,
                "source_signal_id": source_signal_id,
                "runtime_state_hash_before": runtime_state_hash_before,
            },
            diagnostics=[{"stage": "projection_validation", "ok": False, "reason": issue} for issue in issues],
            issues=issues,
        )

    assert next_round is not None
    assert budget is not None
    after_state = deepcopy(before_state)
    after_executed = before_executed + 1
    after_budget = before_budget + budget
    if after_executed < before_executed:
        issues.append("EXECUTED_ROUNDS_DECREASED")
    if after_budget < before_budget:
        issues.append("CUMULATIVE_BUDGET_DECREASED")
    if issues:
        return _result(
            status=STATUS_INVALID,
            before_state=before_state,
            execution_snapshot=execution_snapshot,
            diagnostics=[{"stage": "projection_calculation", "ok": False, "reason": issue} for issue in issues],
            issues=issues,
        )

    created_at = (
        context.get("preview_timestamp")
        or draft.get("created_at")
        or candidate_preview.get("created_at")
        or before_state.get("last_buy_created_at")
    )
    after_state.update({
        "current_buy_round": next_round,
        "executed_buy_rounds": after_executed,
        "cumulative_buy_budget": after_budget,
        "last_buy_order_price": deepcopy(draft.get("price")),
        "last_buy_budget": budget,
        "last_buy_signal_id": deepcopy(source_signal_id),
        "last_buy_candidate_id": candidate_id,
        "last_buy_created_at": deepcopy(created_at),
        "is_last_buy_round": bool(draft.get("is_last_round")),
        "execution_policy_version": deepcopy(draft.get("policy_version")),
        "execution_policy_hash": policy_hash,
        "multi_point_policy_hash": approved_rule_hash,
    })
    runtime_state_hash_after = _stable_hash(after_state)
    changes = _changed_fields(before_state, after_state)
    runtime_patch_preview = {
        "operation": "preview_runtime_state_patch",
        "target": TARGET,
        "changes": changes,
        "source_candidate_id": candidate_id,
        "source_signal_id": deepcopy(source_signal_id),
        "policy_hash": policy_hash,
        "runtime_state_hash_before": runtime_state_hash_before,
        "runtime_state_hash_after_candidate": runtime_state_hash_after,
    }
    runtime_projection = {
        "target": TARGET,
        "candidate_id": candidate_id,
        "source_signal_id": deepcopy(source_signal_id),
        "before_state_hash": runtime_state_hash_before,
        "after_state_candidate_hash": runtime_state_hash_after,
        "changed_fields": sorted(changes),
    }
    return _result(
        status=STATUS_READY,
        runtime_projection=runtime_projection,
        runtime_patch_preview=runtime_patch_preview,
        before_state=before_state,
        after_state_candidate=after_state,
        execution_snapshot=execution_snapshot,
        evidence={
            "candidate_id": candidate_id,
            "source_signal_id": source_signal_id,
            "before_executed_buy_rounds": before_executed,
            "after_executed_buy_rounds": after_executed,
            "before_cumulative_buy_budget": before_budget,
            "after_cumulative_buy_budget": after_budget,
        },
        diagnostics=[{"stage": "runtime_projection", "ok": True, "reason": "projection ready"}],
    )
