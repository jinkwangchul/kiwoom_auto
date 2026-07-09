# -*- coding: utf-8 -*-
"""Runtime Commit Boundary.

This module evaluates the Execution Preview Orchestrator result and produces
a boundary decision before any real runtime commit is attempted.

It never performs real commit, writes runtime files, modifies routines/*/rules.json,
writes SQLite, calls SendOrder, connects Chejan, updates GUI, or commits Git.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


PREVIEW_TYPE = "RUNTIME_COMMIT_BOUNDARY"
STATUS_READY = "RUNTIME_COMMIT_BOUNDARY_READY"
STATUS_BLOCKED = "RUNTIME_COMMIT_BOUNDARY_BLOCKED"
STATUS_INVALID = "RUNTIME_COMMIT_BOUNDARY_INVALID"
ORCHESTRATOR_READY = "ORCHESTRATOR_READY"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


SAFETY_FLAGS = (
    "runtime_write",
    "position_write",
    "balance_write",
    "audit_write",
    "file_write_called",
    "backup_created",
    "rollback_executed",
    "gui_update_called",
    "send_order_called",
    "chejan_called",
    "broker_called",
    "sqlite_write",
    "rules_write",
    "execution_allowed",
    "dispatch_allowed",
    "execution_commit_allowed",
    "runtime_apply_allowed",
)


def _build_eligibility(
    orchestrator_result: dict[str, Any],
) -> dict[str, Any]:
    """Build runtime commit eligibility preview."""
    issues: list[str] = []
    warnings: list[str] = []

    if not orchestrator_result:
        issues.append("orchestrator_result must be a dict")
        return {
            "status": STATUS_INVALID,
            "issues": issues,
            "warnings": warnings,
            "preview_only": True,
        }

    status = _text(orchestrator_result.get("status")).upper()
    if status == "BLOCKED":
        issues.append("orchestrator result is BLOCKED")
        return {
            "status": STATUS_BLOCKED,
            "issues": issues,
            "warnings": warnings,
            "preview_only": True,
        }
    if status == "INVALID":
        issues.append("orchestrator result is INVALID")
        return {
            "status": STATUS_INVALID,
            "issues": issues,
            "warnings": warnings,
            "preview_only": True,
        }
    if status != ORCHESTRATOR_READY:
        issues.append(f"orchestrator status is not {ORCHESTRATOR_READY}")
        return {
            "status": STATUS_INVALID,
            "issues": issues,
            "warnings": warnings,
            "preview_only": True,
        }
    # If status is ORCHESTRATOR_READY but there are other issues (e.g. safety flags), it's INVALID
    # But if status is BLOCKED, we already returned above. So here status is READY or other.
    # We keep the existing logic: if status is not READY, return INVALID.
    # For BLOCKED, we return BLOCKED. For INVALID, we return INVALID.
    # For READY, we continue to check other conditions.
    # The following code is only reached if status == ORCHESTRATOR_READY or some other non-BLOCKED/INVALID status.
    # But we already handled non-READY above as INVALID. So this is only for READY.
    # Actually, the code above returns INVALID for any status != ORCHESTRATOR_READY (except BLOCKED/INVALID which are handled).
    # So we are good.

    if orchestrator_result.get("preview_only") is not True:
        issues.append("orchestrator preview_only must be true")

    final_decision = _as_dict(orchestrator_result.get("final_orchestrator_decision"))
    if final_decision.get("approved") is not True:
        issues.append("final_orchestrator_decision.approved must be true")

    steps = _as_list(orchestrator_result.get("orchestrator_steps"))
    for step in steps:
        if step.get("completed") is not True:
            issues.append(f"orchestrator step {step.get('step_name')} not completed")

    for flag in SAFETY_FLAGS:
        if orchestrator_result.get(flag) is True:
            issues.append(f"orchestrator {flag} must be false")

    ready = not issues
    return {
        "status": STATUS_READY if ready else STATUS_INVALID,
        "issues": issues,
        "warnings": warnings,
        "preview_only": True,
    }


def _build_contract(
    orchestrator_result: dict[str, Any],
    eligibility: dict[str, Any],
) -> dict[str, Any]:
    """Build runtime commit contract."""
    if eligibility.get("status") != STATUS_READY:
        return {
            "contract_status": eligibility.get("status"),
            "commit_candidate": {},
            "atomic_apply_plan": {},
            "verification_plan": {},
            "rollback_plan": {},
            "protected_targets": [],
            "preview_only": True,
        }

    step_results = _as_dict(orchestrator_result.get("step_results"))
    commit_preview = _as_dict(step_results.get("execution_commit_preview"))
    runtime_apply_preview = _as_dict(step_results.get("execution_runtime_apply_preview"))

    final_commit_decision = _as_dict(commit_preview.get("final_commit_decision"))
    final_runtime_apply_decision = _as_dict(runtime_apply_preview.get("final_runtime_apply_decision"))

    commit_candidate = {
        "candidate_id": "RUNTIME_COMMIT_CANDIDATE_001",
        "source": "EXECUTION_COMMIT_PREVIEW",
        "ready": final_commit_decision.get("committed") is True,
        "blocked": final_commit_decision.get("committed") is not True,
        "preview_only": True,
    }

    atomic_apply_plan = {
        "steps": [
            {
                "step_index": 1,
                "step_name": "lock_runtime",
                "description": "Lock runtime files before apply",
                "executed": False,
                "preview_only": True,
            },
            {
                "step_index": 2,
                "step_name": "apply_order_queue",
                "description": "Apply order queue changes",
                "executed": False,
                "preview_only": True,
            },
            {
                "step_index": 3,
                "step_name": "apply_order_executions",
                "description": "Apply order executions changes",
                "executed": False,
                "preview_only": True,
            },
            {
                "step_index": 4,
                "step_name": "apply_order_locks",
                "description": "Apply order locks changes",
                "executed": False,
                "preview_only": True,
            },
            {
                "step_index": 5,
                "step_name": "unlock_runtime",
                "description": "Unlock runtime files after apply",
                "executed": False,
                "preview_only": True,
            },
        ],
        "total_steps": 5,
        "sequence_executed": False,
        "preview_only": True,
    }

    verification_plan = {
        "items": [
            {
                "verification_index": 1,
                "verification_name": "commit_preview_ready",
                "description": "Confirm commit preview is ready",
                "required": True,
                "completed": False,
                "preview_only": True,
            },
            {
                "verification_index": 2,
                "verification_name": "runtime_apply_preview_ready",
                "description": "Confirm runtime apply preview is ready",
                "required": True,
                "completed": False,
                "preview_only": True,
            },
            {
                "verification_index": 3,
                "verification_name": "safety_gate_passed",
                "description": "Confirm safety gate passed",
                "required": True,
                "completed": False,
                "preview_only": True,
            },
            {
                "verification_index": 4,
                "verification_name": "protected_files_unchanged",
                "description": "Confirm protected files unchanged",
                "required": True,
                "completed": False,
                "preview_only": True,
            },
        ],
        "total_items": 4,
        "preview_only": True,
    }

    rollback_plan = {
        "steps": [
            {
                "step_index": 1,
                "step_name": "restore_order_queue",
                "description": "Restore order_queue.json from backup",
                "executed": False,
                "preview_only": True,
            },
            {
                "step_index": 2,
                "step_name": "restore_order_executions",
                "description": "Restore order_executions.json from backup",
                "executed": False,
                "preview_only": True,
            },
            {
                "step_index": 3,
                "step_name": "restore_order_locks",
                "description": "Restore order_locks.json from backup",
                "executed": False,
                "preview_only": True,
            },
        ],
        "total_steps": 3,
        "preview_only": True,
    }

    protected_targets = [
        "runtime/order_queue.json",
        "runtime/order_executions.json",
        "runtime/order_locks.json",
        "routines/*/rules.json",
    ]

    return {
        "contract_status": STATUS_READY,
        "commit_candidate": commit_candidate,
        "atomic_apply_plan": atomic_apply_plan,
        "verification_plan": verification_plan,
        "rollback_plan": rollback_plan,
        "protected_targets": protected_targets,
        "preview_only": True,
    }


def _build_safety_gate(
    orchestrator_result: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Build runtime commit safety gate."""
    issues: list[str] = []
    warnings: list[str] = []

    for flag in SAFETY_FLAGS:
        if orchestrator_result.get(flag) is True:
            issues.append(f"orchestrator {flag} must be false")

    if orchestrator_result.get("preview_only") is not True:
        issues.append("orchestrator preview_only must be true")

    if contract.get("contract_status") != STATUS_READY:
        issues.append("contract status not READY")

    ready = not issues
    return {
        "status": STATUS_READY if ready else STATUS_INVALID,
        "issues": issues,
        "warnings": warnings,
        "preview_only": True,
    }


def _build_review(
    eligibility: dict[str, Any],
    contract: dict[str, Any],
    safety_gate: dict[str, Any],
) -> dict[str, Any]:
    """Build runtime commit review."""
    issues: list[str] = []
    warnings: list[str] = []
    blocked_reasons: list[str] = []
    invalid_reasons: list[str] = []

    for section_name, section in [
        ("eligibility", eligibility),
        ("contract", contract),
        ("safety_gate", safety_gate),
    ]:
        status = section.get("status")
        if status == STATUS_BLOCKED:
            blocked_reasons.append(f"{section_name} blocked")
        elif status == STATUS_INVALID:
            invalid_reasons.append(f"{section_name} invalid")
        issues.extend(section.get("issues", []))
        warnings.extend(section.get("warnings", []))

    if eligibility.get("status") == STATUS_READY and contract.get("contract_status") == STATUS_READY and safety_gate.get("status") == STATUS_READY:
        status = STATUS_READY
    elif blocked_reasons:
        status = STATUS_BLOCKED
    else:
        status = STATUS_INVALID

    return {
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "blocked_reasons": blocked_reasons,
        "invalid_reasons": invalid_reasons,
        "preview_only": True,
    }


def _build_summary(
    eligibility: dict[str, Any],
    contract: dict[str, Any],
    safety_gate: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Build runtime commit boundary summary."""
    return {
        "eligibility_status": eligibility.get("status"),
        "contract_status": contract.get("contract_status"),
        "safety_gate_status": safety_gate.get("status"),
        "review_status": review.get("status"),
        "total_issues": len(eligibility.get("issues", [])) + len(contract.get("issues", [])) + len(safety_gate.get("issues", [])) + len(review.get("issues", [])),
        "total_warnings": len(eligibility.get("warnings", [])) + len(contract.get("warnings", [])) + len(safety_gate.get("warnings", [])) + len(review.get("warnings", [])),
        "preview_only": True,
    }


def _build_final_decision(
    review: dict[str, Any],
) -> dict[str, Any]:
    """Build final runtime commit boundary decision."""
    ready = review.get("status") == STATUS_READY
    return {
        "status": review.get("status"),
        "ready": ready,
        "blocked": review.get("status") == STATUS_BLOCKED,
        "invalid": review.get("status") == STATUS_INVALID,
        "rejection_reason": "; ".join(review.get("blocked_reasons", []) + review.get("invalid_reasons", [])) if not ready else "",
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "broker_called": False,
        "sqlite_write": False,
        "rules_write": False,
        "execution_allowed": False,
        "dispatch_allowed": False,
        "execution_commit_allowed": False,
        "runtime_apply_allowed": False,
        "preview_only": True,
    }


def _result(
    *,
    eligibility: dict[str, Any],
    contract: dict[str, Any],
    safety_gate: dict[str, Any],
    review: dict[str, Any],
    summary: dict[str, Any],
    final_decision: dict[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    """Build the final result dictionary."""
    return {
        "preview_type": PREVIEW_TYPE,
        "status": final_decision.get("status"),
        "preview_only": True,
        "runtime_commit_eligibility": deepcopy(eligibility),
        "runtime_commit_contract": deepcopy(contract),
        "runtime_commit_safety_gate": deepcopy(safety_gate),
        "runtime_commit_review": deepcopy(review),
        "runtime_commit_boundary_summary": deepcopy(summary),
        "final_runtime_commit_boundary_decision": deepcopy(final_decision),
        "generated_at": now or _now_text(),
    }


def evaluate_runtime_commit_boundary(
    orchestrator_result: Any,
    runtime_snapshot: Any = None,
    operator_context: Any = None,
) -> dict[str, Any]:
    """Evaluate the Runtime Commit Boundary from an orchestrator result.

    Args:
        orchestrator_result: Result from build_execution_preview_orchestrator
        runtime_snapshot: Optional runtime snapshot (not used in preview)
        operator_context: Optional operator context (not used in preview)

    Returns:
        Boundary result dict with eligibility, contract, safety_gate, review,
        summary, and final decision.
    """
    orchestrator = deepcopy(_as_dict(orchestrator_result))
    _ = deepcopy(_as_dict(runtime_snapshot))
    _ = deepcopy(_as_dict(operator_context))
    now = _now_text()

    eligibility = _build_eligibility(orchestrator)
    contract = _build_contract(orchestrator, eligibility)
    safety_gate = _build_safety_gate(orchestrator, contract)
    review = _build_review(eligibility, contract, safety_gate)
    summary = _build_summary(eligibility, contract, safety_gate, review)
    final_decision = _build_final_decision(review)

    return _result(
        eligibility=eligibility,
        contract=contract,
        safety_gate=safety_gate,
        review=review,
        summary=summary,
        final_decision=final_decision,
        now=now,
    )