# -*- coding: utf-8 -*-
"""Pure Close/Liquidation policy transition decision service.

The caller owns evidence collection. This module does not read or write files,
inspect Runtime, mutate queues, call GUI code, or invoke broker APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


DOMAIN_CLOSE: Final = "CLOSE"
DOMAIN_LIQUIDATION: Final = "LIQUIDATION"

POLICY_ROUTINE_CLOSE: Final = "루틴매도신호"
POLICY_CARRY_OVER: Final = "이월"
POLICY_MARKET: Final = "시장가"
POLICY_CURRENT_PRICE: Final = "현재가"
POLICY_STOP_LOSS_TAKE_PROFIT: Final = "익절/손절"

REASON_ALLOWED: Final = "ALLOWED"
REASON_SAME_POLICY_NOOP: Final = "SAME_POLICY_NOOP"
REASON_MARKET_DOWNGRADE_NOT_ALLOWED: Final = "MARKET_DOWNGRADE_NOT_ALLOWED"
REASON_RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED: Final = "RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED"
REASON_RETURN_TO_CARRY_OVER_NOT_ALLOWED: Final = "RETURN_TO_CARRY_OVER_NOT_ALLOWED"
REASON_CARRY_OVER_TO_ROUTINE_CLOSE_ACTIVITY_EXISTS: Final = (
    "CARRY_OVER_TO_ROUTINE_CLOSE_ACTIVITY_EXISTS"
)
REASON_INVALID_POLICY_DOMAIN: Final = "INVALID_POLICY_DOMAIN"
REASON_UNKNOWN_CURRENT_POLICY: Final = "UNKNOWN_CURRENT_POLICY"
REASON_UNKNOWN_REQUESTED_POLICY: Final = "UNKNOWN_REQUESTED_POLICY"
REASON_EXECUTION_PROGRESS_BLOCKED: Final = "EXECUTION_PROGRESS_BLOCKED"
REASON_LIQUIDATION_TIME_WINDOW_ENTERED: Final = "LIQUIDATION_TIME_WINDOW_ENTERED"

_CLOSE_POLICIES: Final = frozenset(
    {
        POLICY_ROUTINE_CLOSE,
        POLICY_CARRY_OVER,
        POLICY_MARKET,
        POLICY_CURRENT_PRICE,
        POLICY_STOP_LOSS_TAKE_PROFIT,
    }
)
_LIQUIDATION_POLICIES: Final = frozenset(
    {
        POLICY_CARRY_OVER,
        POLICY_MARKET,
        POLICY_CURRENT_PRICE,
    }
)
_POLICY_ALIASES: Final = {
    "루틴매도신호": POLICY_ROUTINE_CLOSE,
    "루틴매도": POLICY_ROUTINE_CLOSE,
    "루틴": POLICY_ROUTINE_CLOSE,
    "루틴마감": POLICY_ROUTINE_CLOSE,
    "이월": POLICY_CARRY_OVER,
    "시장가": POLICY_MARKET,
    "시장가즉시": POLICY_MARKET,
    "현재가": POLICY_CURRENT_PRICE,
    "현재가즉시": POLICY_CURRENT_PRICE,
    "익절/손절": POLICY_STOP_LOSS_TAKE_PROFIT,
    "손/익절": POLICY_STOP_LOSS_TAKE_PROFIT,
    "익절손절": POLICY_STOP_LOSS_TAKE_PROFIT,
    "손익절": POLICY_STOP_LOSS_TAKE_PROFIT,
    "익/손": POLICY_STOP_LOSS_TAKE_PROFIT,
}


def normalize_direct_close_policy_alias(value: object) -> str:
    """Return the Korean canonical value for direct close aliases only."""
    text = _clean_text(value)
    if text in {"시장가", "시장가즉시"}:
        return POLICY_MARKET
    if text in {"현재가", "현재가즉시"}:
        return POLICY_CURRENT_PRICE
    return text


@dataclass(frozen=True)
class TransitionEvidence:
    routine_close_action_started: bool = False
    actual_order_created: bool = False
    buy_occurred: bool = False
    sell_occurred: bool = False
    pending_order_cancellation_started: bool = False
    liquidation_time_window_entered: bool = False

    @property
    def activity_exists(self) -> bool:
        return any(
            (
                self.routine_close_action_started,
                self.actual_order_created,
                self.buy_occurred,
                self.sell_occurred,
            )
        )


@dataclass(frozen=True)
class TransitionDecision:
    allowed: bool
    reason_code: str
    current_policy: str
    requested_policy: str
    policy_domain: str
    evidence: TransitionEvidence


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_domain(value: object) -> str:
    text = _clean_text(value).upper()
    if text in {DOMAIN_CLOSE, "CLOSE_POLICY", "마감정책"}:
        return DOMAIN_CLOSE
    if text in {DOMAIN_LIQUIDATION, "LIQUIDATION_POLICY", "청산정책"}:
        return DOMAIN_LIQUIDATION
    return text


def _normalize_policy(value: object) -> str:
    normalized = normalize_direct_close_policy_alias(value)
    return _POLICY_ALIASES.get(normalized, normalized)


def is_routine_close_policy(value: object) -> bool:
    """Return whether an existing alias denotes canonical routine close."""
    return _normalize_policy(value) == POLICY_ROUTINE_CLOSE


def _decision(
    *,
    allowed: bool,
    reason_code: str,
    current_policy: str,
    requested_policy: str,
    policy_domain: str,
    evidence: TransitionEvidence,
) -> TransitionDecision:
    return TransitionDecision(
        allowed=allowed,
        reason_code=reason_code,
        current_policy=current_policy,
        requested_policy=requested_policy,
        policy_domain=policy_domain,
        evidence=evidence,
    )


def decide_close_liquidation_transition(
    *,
    policy_domain: object,
    current_policy: object,
    requested_policy: object,
    evidence: TransitionEvidence | None = None,
) -> TransitionDecision:
    """Return a side-effect-free transition decision."""

    snapshot = evidence if isinstance(evidence, TransitionEvidence) else TransitionEvidence()
    domain = _normalize_domain(policy_domain)
    current = _normalize_policy(current_policy)
    requested = _normalize_policy(requested_policy)

    if domain not in {DOMAIN_CLOSE, DOMAIN_LIQUIDATION}:
        return _decision(
            allowed=False,
            reason_code=REASON_INVALID_POLICY_DOMAIN,
            current_policy=current,
            requested_policy=requested,
            policy_domain=domain,
            evidence=snapshot,
        )

    valid_policies = _CLOSE_POLICIES if domain == DOMAIN_CLOSE else _LIQUIDATION_POLICIES
    if current not in valid_policies:
        return _decision(
            allowed=False,
            reason_code=REASON_UNKNOWN_CURRENT_POLICY,
            current_policy=current,
            requested_policy=requested,
            policy_domain=domain,
            evidence=snapshot,
        )
    if requested not in valid_policies:
        return _decision(
            allowed=False,
            reason_code=REASON_UNKNOWN_REQUESTED_POLICY,
            current_policy=current,
            requested_policy=requested,
            policy_domain=domain,
            evidence=snapshot,
        )

    if current == requested:
        return _decision(
            allowed=True,
            reason_code=REASON_SAME_POLICY_NOOP,
            current_policy=current,
            requested_policy=requested,
            policy_domain=domain,
            evidence=snapshot,
        )

    # Individual liquidation is a scheduled stock-policy override. Before its
    # time gate all three policies are freely replaceable; after the gate the
    # policy observed at entry is final for that execution cycle.
    if domain == DOMAIN_LIQUIDATION:
        if snapshot.liquidation_time_window_entered:
            return _decision(
                allowed=False,
                reason_code=REASON_LIQUIDATION_TIME_WINDOW_ENTERED,
                current_policy=current,
                requested_policy=requested,
                policy_domain=domain,
                evidence=snapshot,
            )
        return _decision(
            allowed=True,
            reason_code=REASON_ALLOWED,
            current_policy=current,
            requested_policy=requested,
            policy_domain=domain,
            evidence=snapshot,
        )

    if snapshot.pending_order_cancellation_started and requested in {
        POLICY_ROUTINE_CLOSE,
        POLICY_CARRY_OVER,
    }:
        return _decision(
            allowed=False,
            reason_code=REASON_EXECUTION_PROGRESS_BLOCKED,
            current_policy=current,
            requested_policy=requested,
            policy_domain=domain,
            evidence=snapshot,
        )

    if current == POLICY_MARKET:
        return _decision(
            allowed=False,
            reason_code=REASON_MARKET_DOWNGRADE_NOT_ALLOWED,
            current_policy=current,
            requested_policy=requested,
            policy_domain=domain,
            evidence=snapshot,
        )

    if requested == POLICY_ROUTINE_CLOSE:
        if current == POLICY_CARRY_OVER:
            if snapshot.activity_exists:
                return _decision(
                    allowed=False,
                    reason_code=REASON_CARRY_OVER_TO_ROUTINE_CLOSE_ACTIVITY_EXISTS,
                    current_policy=current,
                    requested_policy=requested,
                    policy_domain=domain,
                    evidence=snapshot,
                )
            return _decision(
                allowed=True,
                reason_code=REASON_ALLOWED,
                current_policy=current,
                requested_policy=requested,
                policy_domain=domain,
                evidence=snapshot,
            )
        return _decision(
            allowed=False,
            reason_code=REASON_RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED,
            current_policy=current,
            requested_policy=requested,
            policy_domain=domain,
            evidence=snapshot,
        )

    if requested == POLICY_CARRY_OVER and current != POLICY_ROUTINE_CLOSE:
        return _decision(
            allowed=False,
            reason_code=REASON_RETURN_TO_CARRY_OVER_NOT_ALLOWED,
            current_policy=current,
            requested_policy=requested,
            policy_domain=domain,
            evidence=snapshot,
        )

    return _decision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        current_policy=current,
        requested_policy=requested,
        policy_domain=domain,
        evidence=snapshot,
    )
