# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from chejan_event_evidence_review import review_chejan_event_evidence


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


class ChejanEventEvidenceReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _evidence_result(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "EVIDENCE_READY",
            "evidence": {
                "evidence_type": "CHEJAN_EVENT_EVIDENCE",
                "evidence_id": "CHEJAN_EVIDENCE_REVIEW_1",
                "candidate_event_type": "ORDER_RECEIVED_CANDIDATE",
                "confidence": "HIGH",
                "identity": {
                    "record_id": "SEND_ORDER_RECORD_REVIEW_1",
                    "order_id": "ORDER_REVIEW_1",
                    "dispatch_id": "DISPATCH_REVIEW_1",
                    "source_signal_id": "SIGNAL_REVIEW_1",
                    "order_queued_id": "ORDER_QUEUED_REVIEW_1",
                    "broker_order_no": "12345",
                },
                "raw_fields": {
                    "source_event_type": "ORDER_OPEN",
                    "raw_order_status": "ORDER_OPEN",
                    "raw_filled_quantity": "0",
                    "raw_remaining_quantity": "10",
                },
                "final_state_confirmed": False,
                "lifecycle_created": False,
                "runtime_write": False,
                "queue_write": False,
                "next_stage": "CHEJAN_EVENT_EVIDENCE_REVIEW_REQUIRED",
            },
            "issues": [],
            "warnings": [],
            "lifecycle_created": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "lifecycle_review_enabled": True,
            "expected_identity": {
                "record_id": "SEND_ORDER_RECORD_REVIEW_1",
                "order_id": "ORDER_REVIEW_1",
                "dispatch_id": "DISPATCH_REVIEW_1",
                "source_signal_id": "SIGNAL_REVIEW_1",
                "order_queued_id": "ORDER_QUEUED_REVIEW_1",
            },
            "operator_review_required": True,
        }
        result.update(overrides)
        return result

    def test_evidence_review_ok_normal(self) -> None:
        result = review_chejan_event_evidence(self._evidence_result(), self._context())

        self.assertEqual("EVIDENCE_REVIEW_OK", result["status"])
        self.assertTrue(result["lifecycle_ready"])
        self.assertFalse(result["lifecycle_created"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        review = result["review"]
        self.assertEqual("CHEJAN_EVIDENCE_REVIEW_1", review["evidence_id"])
        self.assertEqual("ORDER_RECEIVED_CANDIDATE", review["candidate_event_type"])
        self.assertEqual("ORDER_LIFECYCLE_CANDIDATE_REVIEW_REQUIRED", review["next_stage"])
        self.assertFalse(review["final_state_confirmed"])

    def test_evidence_blocked_is_review_blocked(self) -> None:
        result = review_chejan_event_evidence(
            self._evidence_result(status="BLOCKED", issues=["blocked"]),
            self._context(),
        )

        self.assertEqual("EVIDENCE_REVIEW_BLOCKED", result["status"])
        self.assertFalse(result["lifecycle_ready"])

    def test_evidence_invalid_is_invalid(self) -> None:
        result = review_chejan_event_evidence(
            self._evidence_result(status="INVALID", issues=["bad"]),
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["lifecycle_ready"])

    def test_review_disabled_is_blocked(self) -> None:
        result = review_chejan_event_evidence(
            self._evidence_result(),
            self._context(lifecycle_review_enabled=False),
        )

        self.assertEqual("EVIDENCE_REVIEW_BLOCKED", result["status"])
        self.assertIn("review_context.lifecycle_review_enabled is not true", result["issues"])

    def test_identity_mismatch_is_invalid(self) -> None:
        for field, value in (
            ("order_id", "OTHER_ORDER"),
            ("dispatch_id", "OTHER_DISPATCH"),
            ("source_signal_id", "OTHER_SIGNAL"),
        ):
            context = self._context()
            context["expected_identity"][field] = value

            result = review_chejan_event_evidence(self._evidence_result(), context)

            self.assertEqual("INVALID", result["status"])
            self.assertIn(f"identity.{field} does not match review_context", result["issues"])

    def test_unknown_candidate_review_possible(self) -> None:
        evidence = self._evidence_result()
        evidence["evidence"]["candidate_event_type"] = "UNKNOWN_CANDIDATE"

        result = review_chejan_event_evidence(evidence, self._context())

        self.assertEqual("EVIDENCE_REVIEW_OK", result["status"])
        self.assertTrue(result["review"]["unknown_candidate"])
        self.assertTrue(result["lifecycle_ready"])

    def test_missing_candidate_or_identity_is_invalid(self) -> None:
        missing_candidate = self._evidence_result()
        missing_candidate["evidence"]["candidate_event_type"] = ""
        missing_identity = self._evidence_result()
        missing_identity["evidence"]["identity"]["record_id"] = ""

        candidate_result = review_chejan_event_evidence(missing_candidate, self._context())
        identity_result = review_chejan_event_evidence(missing_identity, self._context())

        self.assertEqual("INVALID", candidate_result["status"])
        self.assertEqual("INVALID", identity_result["status"])

    def test_malformed_input_or_context_is_invalid(self) -> None:
        self.assertEqual("INVALID", review_chejan_event_evidence(None, self._context())["status"])
        self.assertEqual("INVALID", review_chejan_event_evidence(self._evidence_result(), {})["status"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        evidence = self._evidence_result()
        context = self._context()
        before = (deepcopy(evidence), deepcopy(context))

        result = review_chejan_event_evidence(evidence, context)
        result["review"]["identity"]["order_id"] = "MUTATED_ORDER"

        self.assertEqual(before, (evidence, context))
        fresh = review_chejan_event_evidence(evidence, context)
        self.assertEqual("ORDER_REVIEW_1", fresh["review"]["identity"]["order_id"])

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        result = review_chejan_event_evidence(self._evidence_result(), self._context())

        self.assertEqual("EVIDENCE_REVIEW_OK", result["status"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
