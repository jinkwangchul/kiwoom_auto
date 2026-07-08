# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from chejan_event_evidence_builder import build_chejan_event_evidence


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


class ChejanEventEvidenceBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _classification(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "CLASSIFICATION_READY",
            "classification_preview": {
                "preview_type": "CHEJAN_EVENT_CLASSIFICATION_PREVIEW",
                "candidate_event_type": "ORDER_RECEIVED_CANDIDATE",
                "confidence": "HIGH",
                "identity": {
                    "record_id": "SEND_ORDER_RECORD_EVIDENCE_1",
                    "order_id": "ORDER_EVIDENCE_1",
                    "dispatch_id": "DISPATCH_EVIDENCE_1",
                    "source_signal_id": "SIGNAL_EVIDENCE_1",
                    "order_queued_id": "ORDER_QUEUED_EVIDENCE_1",
                    "broker_order_no": "12345",
                },
                "source_event_type": "ORDER_OPEN",
                "raw_order_status": "ORDER_OPEN",
                "raw_filled_quantity": "0",
                "raw_remaining_quantity": "10",
                "final_state_confirmed": False,
                "lifecycle_created": False,
                "runtime_write": False,
                "queue_write": False,
            },
            "candidate_event_type": "ORDER_RECEIVED_CANDIDATE",
            "confidence": "HIGH",
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
            "evidence_enabled": True,
            "evidence_source": "classification_preview",
            "operator_review_required": True,
        }
        result.update(overrides)
        return result

    def test_evidence_ready_normal(self) -> None:
        result = build_chejan_event_evidence(self._classification(), self._context())

        self.assertEqual("EVIDENCE_READY", result["status"])
        self.assertFalse(result["lifecycle_created"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        evidence = result["evidence"]
        self.assertTrue(evidence["evidence_id"].startswith("CHEJAN_EVIDENCE_"))
        self.assertEqual("ORDER_RECEIVED_CANDIDATE", evidence["candidate_event_type"])
        self.assertEqual("HIGH", evidence["confidence"])
        self.assertFalse(evidence["final_state_confirmed"])
        self.assertFalse(evidence["position_update_called"])
        self.assertFalse(evidence["balance_update_called"])
        self.assertFalse(evidence["auto_retry_called"])
        self.assertEqual("CHEJAN_EVENT_EVIDENCE_REVIEW_REQUIRED", evidence["next_stage"])

    def test_unknown_candidate_evidence_ready(self) -> None:
        classification = self._classification(candidate_event_type="UNKNOWN_CANDIDATE", confidence="LOW")
        classification["classification_preview"]["candidate_event_type"] = "UNKNOWN_CANDIDATE"
        classification["classification_preview"]["confidence"] = "LOW"

        result = build_chejan_event_evidence(classification, self._context())

        self.assertEqual("EVIDENCE_READY", result["status"])
        self.assertEqual("UNKNOWN_CANDIDATE", result["evidence"]["candidate_event_type"])
        self.assertEqual("LOW", result["evidence"]["confidence"])

    def test_classification_blocked_is_blocked(self) -> None:
        result = build_chejan_event_evidence(
            self._classification(status="BLOCKED", issues=["blocked"]),
            self._context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("classification_preview_result.status is BLOCKED", result["issues"])

    def test_classification_invalid_is_invalid(self) -> None:
        result = build_chejan_event_evidence(
            self._classification(status="INVALID", issues=["bad"]),
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("classification_preview_result.status is INVALID", result["issues"])

    def test_candidate_missing_is_invalid(self) -> None:
        classification = self._classification(candidate_event_type="")

        result = build_chejan_event_evidence(classification, self._context())

        self.assertEqual("INVALID", result["status"])
        self.assertIn("candidate_event_type is required", result["issues"])

    def test_evidence_disabled_is_blocked(self) -> None:
        result = build_chejan_event_evidence(self._classification(), self._context(evidence_enabled=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("evidence_context.evidence_enabled is not true", result["issues"])

    def test_malformed_input_or_context_is_invalid(self) -> None:
        self.assertEqual("INVALID", build_chejan_event_evidence(None, self._context())["status"])
        self.assertEqual("INVALID", build_chejan_event_evidence(self._classification(), {})["status"])

    def test_identity_missing_is_invalid(self) -> None:
        classification = self._classification()
        classification["classification_preview"]["identity"]["dispatch_id"] = ""

        result = build_chejan_event_evidence(classification, self._context())

        self.assertEqual("INVALID", result["status"])
        self.assertIn("identity missing fields: dispatch_id", result["issues"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        classification = self._classification()
        context = self._context()
        before = (deepcopy(classification), deepcopy(context))

        result = build_chejan_event_evidence(classification, context)
        result["evidence"]["identity"]["order_id"] = "MUTATED_ORDER"
        result["evidence"]["classification_preview"]["identity"]["order_id"] = "MUTATED_PREVIEW"

        self.assertEqual(before, (classification, context))
        fresh = build_chejan_event_evidence(classification, context)
        self.assertEqual("ORDER_EVIDENCE_1", fresh["evidence"]["identity"]["order_id"])

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        result = build_chejan_event_evidence(self._classification(), self._context())

        self.assertEqual("EVIDENCE_READY", result["status"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
