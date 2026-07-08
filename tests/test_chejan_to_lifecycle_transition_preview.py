# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from chejan_to_lifecycle_transition_preview import preview_chejan_lifecycle_transition


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


class ChejanToLifecycleTransitionPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _review_result(self, candidate: str = "ORDER_RECEIVED_CANDIDATE", **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "EVIDENCE_REVIEW_OK",
            "review": {
                "review_type": "CHEJAN_EVENT_EVIDENCE_REVIEW",
                "evidence_id": "CHEJAN_EVIDENCE_TRANSITION_1",
                "candidate_event_type": candidate,
                "confidence": "HIGH",
                "identity": {
                    "record_id": "SEND_ORDER_RECORD_TRANSITION_1",
                    "order_id": "ORDER_TRANSITION_1",
                    "dispatch_id": "DISPATCH_TRANSITION_1",
                    "source_signal_id": "SIGNAL_TRANSITION_1",
                    "order_queued_id": "ORDER_QUEUED_TRANSITION_1",
                },
                "raw_fields": {
                    "raw_order_status": "ORDER_OPEN",
                    "raw_filled_quantity": "0",
                    "raw_remaining_quantity": "10",
                },
                "lifecycle_created": False,
                "runtime_write": False,
                "queue_write": False,
                "next_stage": "ORDER_LIFECYCLE_CANDIDATE_REVIEW_REQUIRED",
            },
            "issues": [],
            "warnings": [],
            "lifecycle_ready": True,
            "lifecycle_created": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _policy(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "lifecycle_transition_enabled": True,
            "allowed_lifecycle_events": [
                "ORDER_RECEIVED",
                "ORDER_REJECTED",
                "ORDER_CANCELLED",
                "PARTIAL_FILL",
                "FULL_FILL",
                "UNKNOWN_EVENT",
            ],
        }
        result.update(overrides)
        return result

    def _snapshot(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "snapshot_valid": True,
            "order_id": "ORDER_TRANSITION_1",
            "current_status": "SEND_ORDER_SENT",
            "existing_events": [],
        }
        result.update(overrides)
        return result

    def test_order_received_transition_preview(self) -> None:
        result = preview_chejan_lifecycle_transition(self._review_result(), self._policy(), self._snapshot())

        self.assertEqual("TRANSITION_READY", result["status"])
        self.assertEqual("ORDER_RECEIVED", result["candidate_lifecycle_event"])
        self.assertFalse(result["lifecycle_created"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        preview = result["transition_preview"]
        self.assertEqual("CHEJAN_TO_LIFECYCLE_TRANSITION_PREVIEW", preview["preview_type"])
        self.assertEqual("ORDER_RECEIVED", preview["candidate_lifecycle_event"])
        self.assertEqual("ORDER_TRANSITION_1", preview["identity"]["order_id"])
        self.assertFalse(preview["final_state_confirmed"])

    def test_partial_fill_transition_preview(self) -> None:
        result = preview_chejan_lifecycle_transition(
            self._review_result("PARTIAL_FILL_CANDIDATE"),
            self._policy(),
            self._snapshot(),
        )

        self.assertEqual("TRANSITION_READY", result["status"])
        self.assertEqual("PARTIAL_FILL", result["candidate_lifecycle_event"])

    def test_full_fill_transition_preview(self) -> None:
        result = preview_chejan_lifecycle_transition(
            self._review_result("FULL_FILL_CANDIDATE"),
            self._policy(),
            self._snapshot(),
        )

        self.assertEqual("TRANSITION_READY", result["status"])
        self.assertEqual("FULL_FILL", result["candidate_lifecycle_event"])

    def test_unknown_event_preview_allowed(self) -> None:
        result = preview_chejan_lifecycle_transition(
            self._review_result("UNKNOWN_CANDIDATE"),
            self._policy(),
            self._snapshot(),
        )

        self.assertEqual("TRANSITION_READY", result["status"])
        self.assertEqual("UNKNOWN_EVENT", result["candidate_lifecycle_event"])
        self.assertTrue(result["transition_preview"]["unknown_event"])

    def test_evidence_blocked_is_blocked(self) -> None:
        result = preview_chejan_lifecycle_transition(
            self._review_result(status="EVIDENCE_REVIEW_BLOCKED", issues=["blocked"]),
            self._policy(),
            self._snapshot(),
        )

        self.assertEqual("BLOCKED", result["status"])

    def test_evidence_invalid_is_invalid(self) -> None:
        result = preview_chejan_lifecycle_transition(
            self._review_result(status="INVALID", issues=["bad"]),
            self._policy(),
            self._snapshot(),
        )

        self.assertEqual("INVALID", result["status"])

    def test_transition_disabled_is_blocked(self) -> None:
        result = preview_chejan_lifecycle_transition(
            self._review_result(),
            self._policy(lifecycle_transition_enabled=False),
            self._snapshot(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("lifecycle transition disabled", result["issues"])

    def test_policy_malformed_is_invalid(self) -> None:
        missing_flag = preview_chejan_lifecycle_transition(self._review_result(), {}, self._snapshot())
        malformed_allowed = preview_chejan_lifecycle_transition(
            self._review_result(),
            self._policy(allowed_lifecycle_events="ORDER_RECEIVED"),
            self._snapshot(),
        )

        self.assertEqual("INVALID", missing_flag["status"])
        self.assertEqual("INVALID", malformed_allowed["status"])

    def test_snapshot_malformed_is_invalid(self) -> None:
        missing_snapshot = preview_chejan_lifecycle_transition(self._review_result(), self._policy(), {})
        invalid_snapshot = preview_chejan_lifecycle_transition(
            self._review_result(),
            self._policy(),
            self._snapshot(snapshot_valid=False),
        )
        malformed_events = preview_chejan_lifecycle_transition(
            self._review_result(),
            self._policy(),
            self._snapshot(existing_events={}),
        )

        self.assertEqual("INVALID", missing_snapshot["status"])
        self.assertEqual("INVALID", invalid_snapshot["status"])
        self.assertEqual("INVALID", malformed_events["status"])

    def test_mapping_unavailable_is_invalid(self) -> None:
        result = preview_chejan_lifecycle_transition(
            self._review_result("NOT_A_CANDIDATE"),
            self._policy(),
            self._snapshot(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("candidate_event_type cannot be mapped", result["issues"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        review = self._review_result()
        policy = self._policy()
        snapshot = self._snapshot()
        before = (deepcopy(review), deepcopy(policy), deepcopy(snapshot))

        result = preview_chejan_lifecycle_transition(review, policy, snapshot)
        result["transition_preview"]["identity"]["order_id"] = "MUTATED_ORDER"
        result["transition_preview"]["lifecycle_policy"]["allowed_lifecycle_events"].append("MUTATED_EVENT")
        result["transition_preview"]["current_lifecycle_snapshot"]["existing_events"].append({"event": "MUTATED"})

        self.assertEqual(before, (review, policy, snapshot))
        fresh = preview_chejan_lifecycle_transition(review, policy, snapshot)
        self.assertEqual("ORDER_TRANSITION_1", fresh["transition_preview"]["identity"]["order_id"])
        self.assertNotIn("MUTATED_EVENT", fresh["transition_preview"]["lifecycle_policy"]["allowed_lifecycle_events"])

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        result = preview_chejan_lifecycle_transition(self._review_result(), self._policy(), self._snapshot())

        self.assertEqual("TRANSITION_READY", result["status"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
