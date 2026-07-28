# -*- coding: utf-8 -*-
"""Fail-closed Production guard for Close/Liquidation policy transitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from close_liquidation_transition_service import (
    TransitionDecision,
    decide_close_liquidation_transition,
)
from transition_evidence_reader import (
    TransitionEvidenceBuildResult,
    TransitionEvidenceScope,
    build_transition_evidence,
)


REASON_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
EVIDENCE_COMPLETE = "COMPLETE"
EVIDENCE_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ProductionTransitionGuardResult:
    allowed: bool
    reason_code: str
    evidence_status: str
    evidence: TransitionEvidenceBuildResult
    decision: TransitionDecision | None = None


def evaluate_production_transition(
    *,
    policy_domain: object,
    current_policy: object,
    requested_policy: object,
    queue_path: str | Path,
    fills_path: str | Path,
    runtime_state: dict[str, object] | None,
    runtime_routine_instance_id: object,
    scope: TransitionEvidenceScope,
) -> ProductionTransitionGuardResult:
    """Read evidence and decide without mutating Runtime, Queue, or Commands."""

    evidence = build_transition_evidence(
        queue_path=queue_path,
        fills_path=fills_path,
        runtime_state=runtime_state,
        runtime_routine_instance_id=runtime_routine_instance_id,
        scope=scope,
    )
    snapshot = evidence.to_transition_evidence()
    if snapshot is None:
        return ProductionTransitionGuardResult(
            allowed=False,
            reason_code=REASON_INSUFFICIENT_EVIDENCE,
            evidence_status=EVIDENCE_UNKNOWN,
            evidence=evidence,
        )

    decision = decide_close_liquidation_transition(
        policy_domain=policy_domain,
        current_policy=current_policy,
        requested_policy=requested_policy,
        evidence=snapshot,
    )
    return ProductionTransitionGuardResult(
        allowed=decision.allowed,
        reason_code=decision.reason_code,
        evidence_status=EVIDENCE_COMPLETE,
        evidence=evidence,
        decision=decision,
    )
