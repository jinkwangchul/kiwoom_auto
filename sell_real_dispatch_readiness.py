# -*- coding: utf-8 -*-
"""Final SELL real dispatch readiness integration contract."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"
READINESS_TYPE = "SELL_REAL_DISPATCH_READINESS"

POST_COMMIT_TYPE = "SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER"
RECOVERY_POST_CHECK_TYPE = "SELL_RUNTIME_COMMIT_RECOVERY_POST_CHECK"
QUEUE_REVIEW_TYPE = "SELL_QUEUE_COMMITTED_REVIEW"
DISPATCH_ELIGIBILITY_TYPE = "SELL_DISPATCH_ELIGIBILITY"
BROKER_REQUEST_PREVIEW_TYPE = "SELL_BROKER_REQUEST_PREVIEW"
DISPATCH_APPROVAL_TYPE = "SELL_DISPATCH_APPROVAL_GATE"
FINAL_GUARD_TYPE = "SELL_DISPATCH_FINAL_EXECUTION_GUARD"
SEND_ORDER_CALL_PREVIEW_TYPE = "SELL_SEND_ORDER_CALL_PREVIEW"
AUDIT_PREVIEW_TYPE = "SELL_DISPATCH_EXECUTION_AUDIT_PREVIEW"
EXECUTOR_PLAN_TYPE = "SELL_DISPATCH_EXECUTOR_PLAN"
EXECUTOR_APPROVAL_TYPE = "SELL_DISPATCH_EXECUTOR_APPROVAL_BOUNDARY"
DRYRUN_TYPE = "SELL_DISPATCH_DRY_RUN_EXECUTOR"
POST_EXECUTION_TYPE = "SELL_DISPATCH_POST_EXECUTION_VERIFICATION_PREVIEW"

IDENTITY_FIELDS = (
    "order_id",
    "candidate_id",
    "queue_pending_id",
    "execution_id",
    "request_hash",
    "lock_id",
)
SAFETY_FALSE_FIELDS = (
    "send_order_called",
    "broker_api_called",
    "actual_order_sent",
    "execution_started",
    "queue_write",
    "runtime_write",
    "queue_status_changed",
    "order_request_created",
    "real_ready_state_changed",
)


def build_sell_real_dispatch_readiness(
    post_execution_verification_preview: Any,
    *,
    post_commit_verifier: Any = None,
    recovery_post_check: Any = None,
    queue_committed_review: Any = None,
    dispatch_eligibility: Any = None,
    broker_request_preview: Any = None,
    dispatch_approval_gate: Any = None,
    final_guard: Any = None,
    send_order_call_preview: Any = None,
    dispatch_audit_preview: Any = None,
    executor_plan: Any = None,
    executor_approval_boundary: Any = None,
    dry_run_executor: Any = None,
) -> dict[str, Any]:
    """Integrate existing SELL commit/recovery/dispatch previews into one result."""
    result = _base_result(post_execution_verification_preview)
    if not isinstance(post_execution_verification_preview, dict):
        return _finish(result, INVALID, "post_execution_verification_preview must be a dict")
    if post_execution_verification_preview.get("verification_type") != POST_EXECUTION_TYPE:
        return _finish(result, INVALID, "verification_type is not SELL_DISPATCH_POST_EXECUTION_VERIFICATION_PREVIEW")

    dry_run = _as_dict(dry_run_executor) or _as_dict(post_execution_verification_preview.get("source_dry_run_snapshot"))
    executor_boundary = _as_dict(executor_approval_boundary) or _as_dict(dry_run.get("source_approval_boundary_snapshot"))
    plan = _as_dict(executor_plan) or _as_dict(executor_boundary.get("source_plan_snapshot"))
    audit = _as_dict(dispatch_audit_preview) or _as_dict(plan.get("source_audit_snapshot"))
    call_preview = _as_dict(send_order_call_preview) or _as_dict(audit.get("source_call_preview_snapshot"))
    guard = _as_dict(final_guard) or _as_dict(call_preview.get("source_final_guard_snapshot"))
    approval = _as_dict(dispatch_approval_gate) or _as_dict(guard.get("source_approval_snapshot"))
    broker = _as_dict(broker_request_preview) or _as_dict(approval.get("source_broker_request_snapshot"))
    eligibility = _as_dict(dispatch_eligibility) or _as_dict(broker.get("source_eligibility_snapshot"))
    queue_review = _as_dict(queue_committed_review) or _as_dict(eligibility.get("source_review_snapshot"))
    post_commit = _as_dict(post_commit_verifier) or _as_dict(queue_review.get("source_verifier_snapshot"))
    recovery = _as_dict(recovery_post_check)

    chain = {
        "post_commit_verifier": post_commit,
        "recovery_post_check": recovery,
        "queue_committed_review": queue_review,
        "dispatch_eligibility": eligibility,
        "broker_request_preview": broker,
        "dispatch_approval_gate": approval,
        "final_guard": guard,
        "send_order_call_preview": call_preview,
        "dispatch_audit_preview": audit,
        "executor_plan": plan,
        "executor_approval_boundary": executor_boundary,
        "dry_run_executor": dry_run,
        "post_execution_verification_preview": post_execution_verification_preview,
    }

    chain_error = _validate_chain(chain)
    if chain_error:
        return _finish(result, chain_error[0], chain_error[1])

    identities = _identity_order(post_execution_verification_preview.get("verified_candidate_results"))
    if not identities:
        return _finish(result, INVALID, "candidate identity order is required")
    order_error = _identity_order_error(chain, identities)
    if order_error:
        return _finish(result, INVALID, order_error)

    queue_path = _text(plan.get("queue_path") or audit.get("queue_path") or post_execution_verification_preview.get("queue_path"))
    masked_account = _text(plan.get("masked_account_no") or audit.get("masked_account_no"))
    approval_token_hash = _text(executor_boundary.get("approval_token_hash") or guard.get("approval_token_hash") or audit.get("approval_token_hash"))
    dispatch_plan_hash = _text(plan.get("plan_hash"))
    dispatch_preview_hash = _text(audit.get("send_order_call_preview_hash") or plan.get("send_order_call_preview_hash"))
    audit_hash = _stable_hash(audit)
    chain_hash = _stable_hash(
        {
            "candidate_identity_order": identities,
            "queue_path": queue_path,
            "masked_account": masked_account,
            "approval_token_hash": approval_token_hash,
            "dispatch_plan_hash": dispatch_plan_hash,
            "dispatch_preview_hash": dispatch_preview_hash,
            "audit_hash": audit_hash,
        }
    )

    result.update(
        {
            "status": READY,
            "real_dispatch_ready": True,
            "readiness_hash": _stable_hash({"type": READINESS_TYPE, "chain_hash": chain_hash}),
            "chain_hash": chain_hash,
            "candidate_count": len(identities),
            "candidate_identity_order": deepcopy(identities),
            "queue_path": queue_path,
            "masked_account": masked_account,
            "approval_token_hash": approval_token_hash,
            "dispatch_plan_hash": dispatch_plan_hash,
            "dispatch_preview_hash": dispatch_preview_hash,
            "audit_hash": audit_hash,
            "source_chain_snapshot": _sanitize_chain(chain),
            "summary": {
                "candidate_count": len(identities),
                "all_chain_ready": True,
                "recovery_required": False,
                "partial_ready": False,
                "send_order_called": False,
                "actual_order_sent": False,
            },
        }
    )
    return _finalize(result)


def _validate_chain(chain: dict[str, dict[str, Any]]) -> tuple[str, str] | None:
    expected = (
        ("post_commit_verifier", "verifier_type", POST_COMMIT_TYPE, "post_commit_verified"),
        ("queue_committed_review", "review_type", QUEUE_REVIEW_TYPE, "queue_committed_review_ready"),
        ("dispatch_eligibility", "eligibility_type", DISPATCH_ELIGIBILITY_TYPE, "dispatch_eligible"),
        ("broker_request_preview", "preview_type", BROKER_REQUEST_PREVIEW_TYPE, "broker_request_preview_ready"),
        ("dispatch_approval_gate", "approval_type", DISPATCH_APPROVAL_TYPE, "approval_granted"),
        ("final_guard", "guard_type", FINAL_GUARD_TYPE, "final_guard_ready"),
        ("send_order_call_preview", "preview_type", SEND_ORDER_CALL_PREVIEW_TYPE, "send_order_call_preview_ready"),
        ("dispatch_audit_preview", "audit_type", AUDIT_PREVIEW_TYPE, "audit_preview_ready"),
        ("executor_plan", "plan_type", EXECUTOR_PLAN_TYPE, "executor_plan_ready"),
        ("executor_approval_boundary", "approval_type", EXECUTOR_APPROVAL_TYPE, "execution_allowed"),
        ("dry_run_executor", "executor_type", DRYRUN_TYPE, "dry_run_ready"),
        ("post_execution_verification_preview", "verification_type", POST_EXECUTION_TYPE, "post_execution_verified"),
    )
    for label, type_field, type_value, ready_field in expected:
        item = chain.get(label)
        if not item:
            return INVALID, f"{label} is required"
        if item.get(type_field) != type_value:
            return INVALID, f"{label}.{type_field} mismatch"
        if _has_safety_violation(item):
            return INVALID, f"{label} safety flag violation"
        status = _status(item.get("status"))
        if status == INVALID:
            return INVALID, f"{label} status is INVALID"
        if status != READY:
            return BLOCKED, f"{label} is not READY"
        if item.get(ready_field) is not True:
            return INVALID, f"{label}.{ready_field} must be True"

    recovery = chain.get("recovery_post_check") or {}
    if recovery:
        if recovery.get("post_check_type") != RECOVERY_POST_CHECK_TYPE:
            return INVALID, "recovery_post_check.post_check_type mismatch"
        if recovery.get("recovery_required") is True or recovery.get("status") == "RECOVERY_READY":
            return BLOCKED, "recovery is required"
        if recovery.get("status") == INVALID:
            return INVALID, "recovery_post_check status is INVALID"

    post_commit = chain["post_commit_verifier"]
    if post_commit.get("post_commit_file_verified") is not True:
        return INVALID, "post_commit_file_verified must be True"
    hash_error = _chain_hash_error(chain)
    if hash_error:
        return INVALID, hash_error
    return None


def _chain_hash_error(chain: dict[str, dict[str, Any]]) -> str | None:
    call_preview_hash = _stable_hash(chain["send_order_call_preview"])
    audit = chain["dispatch_audit_preview"]
    if audit.get("send_order_call_preview_hash") != call_preview_hash:
        return "dispatch_audit_preview send_order_call_preview_hash mismatch"

    plan = chain["executor_plan"]
    if plan.get("send_order_call_preview_hash") != audit.get("send_order_call_preview_hash"):
        return "executor_plan send_order_call_preview_hash mismatch"
    expected_plan_hash = _stable_hash(
        {
            "queue_path": plan.get("queue_path"),
            "masked_account_no": plan.get("masked_account_no"),
            "send_order_call_preview_hash": plan.get("send_order_call_preview_hash"),
            "actions": plan.get("execution_actions"),
        }
    )
    if plan.get("plan_hash") != expected_plan_hash:
        return "executor_plan plan_hash mismatch"
    return None


def _identity_order_error(chain: dict[str, dict[str, Any]], expected: list[dict[str, str]]) -> str | None:
    sources = {
        "queue_committed_review": chain["queue_committed_review"].get("candidate_ids"),
        "broker_request_preview": chain["broker_request_preview"].get("candidate_ids"),
        "dispatch_approval_gate": chain["dispatch_approval_gate"].get("approved_candidate_ids"),
        "final_guard": chain["final_guard"].get("candidate_ids"),
        "send_order_call_preview": chain["send_order_call_preview"].get("candidate_ids"),
        "executor_plan": chain["executor_plan"].get("candidate_ids"),
        "executor_approval_boundary": chain["executor_approval_boundary"].get("approved_candidate_ids"),
        "dry_run_executor": chain["dry_run_executor"].get("simulated_candidate_ids"),
    }
    expected_ids = [item["candidate_id"] for item in expected]
    for label, candidate_ids in sources.items():
        if candidate_ids != expected_ids:
            return f"{label} candidate order mismatch"
    if chain["executor_plan"].get("summary", {}).get("expected_dispatch_count") != len(expected):
        return "executor_plan expected_dispatch_count mismatch"
    if chain["dry_run_executor"].get("simulated_dispatch_count") != len(expected):
        return "dry_run simulated_dispatch_count mismatch"
    if chain["post_execution_verification_preview"].get("summary", {}).get("verified_count") != len(expected):
        return "post execution verified_count mismatch"
    return None


def _identity_order(items: Any) -> list[dict[str, str]]:
    order: list[dict[str, str]] = []
    for item in _as_list(items):
        identity = _as_dict(item.get("identity")) if isinstance(item, dict) else {}
        if not identity:
            return []
        normalized = {field: _text(identity.get(field)) for field in IDENTITY_FIELDS}
        if any(not value for value in normalized.values()):
            return []
        order.append(normalized)
    return order


def _sanitize_chain(chain: dict[str, dict[str, Any]]) -> dict[str, Any]:
    snapshot = deepcopy(chain)
    # No raw token fields are needed in the final integration snapshot.
    for item in snapshot.values():
        if isinstance(item, dict):
            item.pop("approval_token", None)
    return snapshot


def _base_result(source: Any) -> dict[str, Any]:
    return {
        "readiness_type": READINESS_TYPE,
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Real Dispatch Readiness",
        "routine_dependency": None,
        "status": BLOCKED,
        "real_dispatch_ready": False,
        "preview_only": True,
        "execution_connected": False,
        "send_order_called": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "execution_started": False,
        "queue_write": False,
        "runtime_write": False,
        "queue_status_changed": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "readiness_hash": "",
        "chain_hash": "",
        "candidate_count": 0,
        "candidate_identity_order": [],
        "queue_path": None,
        "masked_account": "",
        "approval_token_hash": "",
        "dispatch_plan_hash": "",
        "dispatch_preview_hash": "",
        "audit_hash": "",
        "source_chain_snapshot": {},
        "reasons": [],
        "warnings": _as_list(source.get("warnings")) if isinstance(source, dict) else [],
        "summary": {},
    }


def _finish(result: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    result["status"] = status
    result["reasons"].append(reason)
    return _finalize(result)


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    for field in SAFETY_FALSE_FIELDS:
        result[field] = False
    result["execution_connected"] = False
    result["preview_only"] = True
    return result


def _has_safety_violation(payload: dict[str, Any]) -> bool:
    return any(payload.get(field) is True for field in SAFETY_FALSE_FIELDS)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _status(value: Any) -> str:
    return _text(value).upper()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
