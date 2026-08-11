from __future__ import annotations

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import tempfile
import unittest

from close_liquidation_transition_service import (
    DOMAIN_CLOSE,
    DOMAIN_LIQUIDATION,
    POLICY_CARRY_OVER,
    POLICY_CURRENT_PRICE,
    POLICY_MARKET,
    POLICY_ROUTINE_CLOSE,
    POLICY_STOP_LOSS_TAKE_PROFIT,
    REASON_ALLOWED,
    REASON_CARRY_OVER_TO_ROUTINE_CLOSE_ACTIVITY_EXISTS,
    REASON_EXECUTION_PROGRESS_BLOCKED,
    REASON_INVALID_POLICY_DOMAIN,
    REASON_MARKET_DOWNGRADE_NOT_ALLOWED,
    REASON_RETURN_TO_CARRY_OVER_NOT_ALLOWED,
    REASON_RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED,
    REASON_SAME_POLICY_NOOP,
    REASON_UNKNOWN_CURRENT_POLICY,
    REASON_UNKNOWN_REQUESTED_POLICY,
    TransitionEvidence,
    decide_close_liquidation_transition,
    normalize_direct_close_policy_alias,
)


class CloseLiquidationTransitionServiceTest(unittest.TestCase):
    def test_direct_close_aliases_use_korean_canonical_values(self) -> None:
        self.assertEqual("시장가", normalize_direct_close_policy_alias("시장가즉시"))
        self.assertEqual("현재가", normalize_direct_close_policy_alias("현재가즉시"))
        self.assertEqual("시장가", normalize_direct_close_policy_alias("시장가"))
        self.assertEqual("현재가", normalize_direct_close_policy_alias("현재가"))
        self.assertEqual("루틴", normalize_direct_close_policy_alias("루틴"))

    def decide(
        self,
        current: object,
        requested: object,
        *,
        domain: object = DOMAIN_CLOSE,
        evidence: TransitionEvidence | None = None,
    ):
        return decide_close_liquidation_transition(
            policy_domain=domain,
            current_policy=current,
            requested_policy=requested,
            evidence=evidence,
        )

    def assert_allowed(
        self,
        current: object,
        requested: object,
        *,
        domain: object = DOMAIN_CLOSE,
        evidence: TransitionEvidence | None = None,
        reason: str = REASON_ALLOWED,
    ) -> None:
        result = self.decide(current, requested, domain=domain, evidence=evidence)
        self.assertTrue(result.allowed)
        self.assertEqual(reason, result.reason_code)

    def assert_blocked(
        self,
        current: object,
        requested: object,
        reason: str,
        *,
        domain: object = DOMAIN_CLOSE,
        evidence: TransitionEvidence | None = None,
    ) -> None:
        result = self.decide(current, requested, domain=domain, evidence=evidence)
        self.assertFalse(result.allowed)
        self.assertEqual(reason, result.reason_code)

    def test_routine_close_all_forward_transitions_allowed(self) -> None:
        for requested in (
            POLICY_CARRY_OVER,
            POLICY_MARKET,
            POLICY_CURRENT_PRICE,
            POLICY_STOP_LOSS_TAKE_PROFIT,
        ):
            with self.subTest(requested=requested):
                self.assert_allowed(POLICY_ROUTINE_CLOSE, requested)

    def test_carry_over_to_routine_close_allowed_without_activity(self) -> None:
        self.assert_allowed(POLICY_CARRY_OVER, POLICY_ROUTINE_CLOSE)

    def test_carry_over_to_routine_close_blocked_by_each_activity(self) -> None:
        evidence_cases = (
            TransitionEvidence(routine_close_action_started=True),
            TransitionEvidence(actual_order_created=True),
            TransitionEvidence(buy_occurred=True),
            TransitionEvidence(sell_occurred=True),
        )
        for evidence in evidence_cases:
            with self.subTest(evidence=evidence):
                self.assert_blocked(
                    POLICY_CARRY_OVER,
                    POLICY_ROUTINE_CLOSE,
                    REASON_CARRY_OVER_TO_ROUTINE_CLOSE_ACTIVITY_EXISTS,
                    evidence=evidence,
                )

    def test_carry_over_forward_transitions_allowed(self) -> None:
        for requested in (
            POLICY_MARKET,
            POLICY_CURRENT_PRICE,
            POLICY_STOP_LOSS_TAKE_PROFIT,
        ):
            with self.subTest(requested=requested):
                self.assert_allowed(POLICY_CARRY_OVER, requested)

    def test_current_price_to_profit_loss_and_market_allowed(self) -> None:
        self.assert_allowed(POLICY_CURRENT_PRICE, POLICY_STOP_LOSS_TAKE_PROFIT)
        self.assert_allowed(POLICY_CURRENT_PRICE, POLICY_MARKET)

    def test_profit_loss_to_current_price_and_market_allowed(self) -> None:
        self.assert_allowed(POLICY_STOP_LOSS_TAKE_PROFIT, POLICY_CURRENT_PRICE)
        self.assert_allowed(POLICY_STOP_LOSS_TAKE_PROFIT, POLICY_MARKET)

    def test_market_cannot_transition_to_other_close_policy(self) -> None:
        for requested in (
            POLICY_ROUTINE_CLOSE,
            POLICY_CARRY_OVER,
            POLICY_CURRENT_PRICE,
            POLICY_STOP_LOSS_TAKE_PROFIT,
        ):
            with self.subTest(requested=requested):
                self.assert_blocked(
                    POLICY_MARKET,
                    requested,
                    REASON_MARKET_DOWNGRADE_NOT_ALLOWED,
                )

    def test_current_and_profit_loss_cannot_return_to_routine_close(self) -> None:
        for current in (POLICY_CURRENT_PRICE, POLICY_STOP_LOSS_TAKE_PROFIT):
            with self.subTest(current=current):
                self.assert_blocked(
                    current,
                    POLICY_ROUTINE_CLOSE,
                    REASON_RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED,
                )

    def test_current_and_profit_loss_cannot_return_to_carry_over(self) -> None:
        for current in (POLICY_CURRENT_PRICE, POLICY_STOP_LOSS_TAKE_PROFIT):
            with self.subTest(current=current):
                self.assert_blocked(
                    current,
                    POLICY_CARRY_OVER,
                    REASON_RETURN_TO_CARRY_OVER_NOT_ALLOWED,
                )

    def test_close_same_policy_is_allowed_noop(self) -> None:
        for policy in (
            POLICY_ROUTINE_CLOSE,
            POLICY_CARRY_OVER,
            POLICY_MARKET,
            POLICY_CURRENT_PRICE,
            POLICY_STOP_LOSS_TAKE_PROFIT,
        ):
            with self.subTest(policy=policy):
                self.assert_allowed(policy, policy, reason=REASON_SAME_POLICY_NOOP)

    def test_liquidation_carry_over_to_current_and_market_allowed(self) -> None:
        self.assert_allowed(
            POLICY_CARRY_OVER,
            POLICY_CURRENT_PRICE,
            domain=DOMAIN_LIQUIDATION,
        )
        self.assert_allowed(
            POLICY_CARRY_OVER,
            POLICY_MARKET,
            domain=DOMAIN_LIQUIDATION,
        )

    def test_liquidation_current_price_to_market_allowed(self) -> None:
        self.assert_allowed(
            POLICY_CURRENT_PRICE,
            POLICY_MARKET,
            domain=DOMAIN_LIQUIDATION,
        )

    def test_liquidation_market_cannot_downgrade(self) -> None:
        for requested in (POLICY_CURRENT_PRICE, POLICY_CARRY_OVER):
            with self.subTest(requested=requested):
                self.assert_blocked(
                    POLICY_MARKET,
                    requested,
                    REASON_MARKET_DOWNGRADE_NOT_ALLOWED,
                    domain=DOMAIN_LIQUIDATION,
                )

    def test_liquidation_current_price_cannot_return_to_carry_over(self) -> None:
        self.assert_blocked(
            POLICY_CURRENT_PRICE,
            POLICY_CARRY_OVER,
            REASON_RETURN_TO_CARRY_OVER_NOT_ALLOWED,
            domain=DOMAIN_LIQUIDATION,
        )

    def test_liquidation_same_policy_is_allowed_noop(self) -> None:
        for policy in (POLICY_CARRY_OVER, POLICY_MARKET, POLICY_CURRENT_PRICE):
            with self.subTest(policy=policy):
                self.assert_allowed(
                    policy,
                    policy,
                    domain=DOMAIN_LIQUIDATION,
                    reason=REASON_SAME_POLICY_NOOP,
                )

    def test_cancellation_progress_blocks_return_to_routine_or_carry(self) -> None:
        evidence = TransitionEvidence(pending_order_cancellation_started=True)
        self.assert_blocked(
            POLICY_CURRENT_PRICE,
            POLICY_ROUTINE_CLOSE,
            REASON_EXECUTION_PROGRESS_BLOCKED,
            evidence=evidence,
        )
        self.assert_blocked(
            POLICY_CURRENT_PRICE,
            POLICY_CARRY_OVER,
            REASON_EXECUTION_PROGRESS_BLOCKED,
            evidence=evidence,
        )

    def test_cancellation_progress_keeps_forward_transitions_allowed(self) -> None:
        evidence = TransitionEvidence(pending_order_cancellation_started=True)
        self.assert_allowed(
            POLICY_CURRENT_PRICE,
            POLICY_STOP_LOSS_TAKE_PROFIT,
            evidence=evidence,
        )
        self.assert_allowed(
            POLICY_STOP_LOSS_TAKE_PROFIT,
            POLICY_CURRENT_PRICE,
            evidence=evidence,
        )
        self.assert_allowed(
            POLICY_CURRENT_PRICE,
            POLICY_MARKET,
            evidence=evidence,
        )
        self.assert_allowed(
            POLICY_STOP_LOSS_TAKE_PROFIT,
            POLICY_MARKET,
            evidence=evidence,
        )

    def test_invalid_domain_blocked(self) -> None:
        self.assert_blocked(
            POLICY_CARRY_OVER,
            POLICY_MARKET,
            REASON_INVALID_POLICY_DOMAIN,
            domain="UNKNOWN",
        )

    def test_unknown_current_policy_blocked(self) -> None:
        self.assert_blocked(
            "알수없음",
            POLICY_MARKET,
            REASON_UNKNOWN_CURRENT_POLICY,
        )

    def test_unknown_requested_policy_blocked(self) -> None:
        self.assert_blocked(
            POLICY_CARRY_OVER,
            "알수없음",
            REASON_UNKNOWN_REQUESTED_POLICY,
        )

    def test_policy_outside_liquidation_domain_is_unknown_for_that_domain(self) -> None:
        self.assert_blocked(
            POLICY_CARRY_OVER,
            POLICY_STOP_LOSS_TAKE_PROFIT,
            REASON_UNKNOWN_REQUESTED_POLICY,
            domain=DOMAIN_LIQUIDATION,
        )

    def test_existing_policy_aliases_normalize_to_storage_values(self) -> None:
        result = self.decide("루틴마감", "손/익절")
        self.assertTrue(result.allowed)
        self.assertEqual(POLICY_ROUTINE_CLOSE, result.current_policy)
        self.assertEqual(POLICY_STOP_LOSS_TAKE_PROFIT, result.requested_policy)

    def test_input_snapshot_is_immutable_and_unchanged(self) -> None:
        evidence = TransitionEvidence(actual_order_created=True)
        before = evidence
        result = self.decide(POLICY_CARRY_OVER, POLICY_MARKET, evidence=evidence)

        self.assertIs(before, result.evidence)
        self.assertEqual(before, evidence)
        with self.assertRaises(FrozenInstanceError):
            evidence.buy_occurred = True  # type: ignore[misc]

    def test_service_creates_no_files_or_runtime_state(self) -> None:
        previous_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                before = tuple(Path(temp_dir).iterdir())
                self.assert_allowed(POLICY_CARRY_OVER, POLICY_MARKET)
                after = tuple(Path(temp_dir).iterdir())
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
