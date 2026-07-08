# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from lifecycle_transition_approval_gate import evaluate_lifecycle_transition_approval


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


class LifecycleTransitionApprovalGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _transition(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "TRANSITION_READY",
            "transition_preview": {
                "preview_type": "CHEJAN_TO_LIFECYCLE_TRANSITION_PREVIEW",
                "evidence_id": "CHEJAN_EVIDENCE_APPROVAL_1",
                "candidate_event_type": "ORDER_RECEIVED_CANDIDATE",
                "candidate_lifecycle_event": "ORDER_RECEIVED",
                "identity": {
                    "record_id": "SEND_ORDER_RECORD_APPROVAL_1",
                    "order_id": "ORDER_APPROVAL_1",
                    "dispatch_id": "DISPATCH_APPROVAL_1",
                    "source_signal_id": "SIGNAL_APPROVAL_1",
                    "order_queued_id": "ORDER_QUEUED_APPROVAL_1",
                },
                "confidence": "HIGH",
                "unknown_event": False,
                "lifecycle_created": False,
                "final_state_confirmed": False,
                "runtime_write": False,
                "queue_write": False,
                "next_stage": "ORDER_LIFECYCLE_TRANSITION_REVIEW_REQUIRED",
            },
            "candidate_lifecycle_event": "ORDER_RECEIVED",
            "issues": [],
            "warnings": [],
            "lifecycle_created": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _policy(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "lifecycle_approval_enabled": True,
            "approval_allowed": True,
            "allowed_lifecycle_events": ["ORDER_RECEIVED", "PARTIAL_FILL", "FULL_FILL", "UNKNOWN_EVENT"],
        }
        result.update(overrides)
        return result

    def _operator(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "operator_lifecycle_approved": True,
            "operator_id": "operator-1",
            "emergency_stop": False,
        }
        result.update(overrides)
        return result

    def test_lifecycle_approved_normal(self) -> None:
        result = evaluate_lifecycle_transition_approval(self._transition(), self._policy(), self._operator())

        self.assertEqual("LIFECYCLE_APPROVED", result["status"])
        self.assertTrue(result["lifecycle_write_allowed"])
        self.assertFalse(result["lifecycle_created"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        approval = result["approval"]
        self.assertTrue(approval["approved"])
        self.assertEqual("ORDER_RECEIVED", approval["candidate_lifecycle_event"])
        self.assertEqual("ORDER_LIFECYCLE_WRITE_REQUIRED", approval["next_stage"])

    def test_transition_blocked_is_denied(self) -> None:
        result = evaluate_lifecycle_transition_approval(
            self._transition(status="BLOCKED", issues=["blocked"]),
            self._policy(),
            self._operator(),
        )

        self.assertEqual("DENIED", result["status"])
        self.assertFalse(result["lifecycle_write_allowed"])

    def test_transition_invalid_is_invalid(self) -> None:
        result = evaluate_lifecycle_transition_approval(
            self._transition(status="INVALID", issues=["bad"]),
            self._policy(),
            self._operator(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["lifecycle_write_allowed"])

    def test_policy_reject_is_denied(self) -> None:
        result = evaluate_lifecycle_transition_approval(
            self._transition(),
            self._policy(approval_allowed=False),
            self._operator(),
        )

        self.assertEqual("DENIED", result["status"])
        self.assertIn("lifecycle approval policy rejected", result["issues"])

    def test_operator_approval_false_is_denied(self) -> None:
        result = evaluate_lifecycle_transition_approval(
            self._transition(),
            self._policy(),
            self._operator(operator_lifecycle_approved=False),
        )

        self.assertEqual("DENIED", result["status"])
        self.assertIn("operator lifecycle approval is missing", result["issues"])

    def test_policy_malformed_is_invalid(self) -> None:
        missing_policy = evaluate_lifecycle_transition_approval(self._transition(), {}, self._operator())
        missing_decision = evaluate_lifecycle_transition_approval(
            self._transition(),
            {"lifecycle_approval_enabled": True},
            self._operator(),
        )
        invalid_status = evaluate_lifecycle_transition_approval(
            self._transition(),
            {"lifecycle_approval_enabled": True, "status": "INVALID"},
            self._operator(),
        )

        self.assertEqual("INVALID", missing_policy["status"])
        self.assertEqual("INVALID", missing_decision["status"])
        self.assertEqual("INVALID", invalid_status["status"])

    def test_malformed_input_is_invalid(self) -> None:
        missing_transition = evaluate_lifecycle_transition_approval(None, self._policy(), self._operator())
        missing_operator = evaluate_lifecycle_transition_approval(self._transition(), self._policy(), {})
        missing_preview = evaluate_lifecycle_transition_approval(
            self._transition(transition_preview={}),
            self._policy(),
            self._operator(),
        )

        self.assertEqual("INVALID", missing_transition["status"])
        self.assertEqual("INVALID", missing_operator["status"])
        self.assertEqual("INVALID", missing_preview["status"])

    def test_identity_or_event_mismatch_is_invalid(self) -> None:
        mismatched_event = self._transition(candidate_lifecycle_event="PARTIAL_FILL")
        missing_identity = self._transition()
        missing_identity["transition_preview"]["identity"]["order_id"] = ""

        event_result = evaluate_lifecycle_transition_approval(mismatched_event, self._policy(), self._operator())
        identity_result = evaluate_lifecycle_transition_approval(missing_identity, self._policy(), self._operator())

        self.assertEqual("INVALID", event_result["status"])
        self.assertEqual("INVALID", identity_result["status"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        transition = self._transition()
        policy = self._policy()
        operator = self._operator()
        before = (deepcopy(transition), deepcopy(policy), deepcopy(operator))

        result = evaluate_lifecycle_transition_approval(transition, policy, operator)
        result["approval"]["transition_preview"]["identity"]["order_id"] = "MUTATED_ORDER"
        result["approval"]["policy"]["allowed_lifecycle_events"].append("MUTATED")
        result["approval"]["operator_context"]["operator_id"] = "mutated"

        self.assertEqual(before, (transition, policy, operator))
        fresh = evaluate_lifecycle_transition_approval(transition, policy, operator)
        self.assertEqual("ORDER_APPROVAL_1", fresh["approval"]["identity"]["order_id"])
        self.assertNotIn("MUTATED", fresh["approval"]["policy"]["allowed_lifecycle_events"])

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        result = evaluate_lifecycle_transition_approval(self._transition(), self._policy(), self._operator())

        self.assertEqual("LIFECYCLE_APPROVED", result["status"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
