# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from chejan_event_classification_preview import preview_chejan_event_classification


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


class ChejanEventClassificationPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _event(self, **raw_overrides: object) -> dict[str, object]:
        raw = {
            "source": "kiwoom_chejan",
            "gubun": "0",
            "event_type": "ORDER_OPEN",
            "order_id": "ORDER_CLASSIFY_1",
            "dispatch_id": "DISPATCH_CLASSIFY_1",
            "source_signal_id": "SIGNAL_CLASSIFY_1",
            "broker_order_no": "12345",
            "received_at": "2026-07-07 10:00:00",
            "fid_values": {
                "9201": "12345678",
                "9203": "12345",
                "9001": "A003550",
                "913": "ORDER_OPEN",
                "911": "0",
                "902": "10",
            },
        }
        raw.update(raw_overrides)
        return {
            "status": "CHEJAN_EVENT_READY",
            "chejan_event_contract": {
                "contract_type": "CHEJAN_RAW_EVENT_CONTRACT",
                "status": "RAW_CHEJAN_EVENT_RECEIVED",
                "event_type": raw.get("event_type"),
                "gubun": raw.get("gubun"),
                "received_at": raw.get("received_at"),
                "source": "kiwoom_chejan",
                "raw_chejan_event": raw,
                "identity": {
                    "record_id": "SEND_ORDER_RECORD_CLASSIFY_1",
                    "order_id": "ORDER_CLASSIFY_1",
                    "dispatch_id": "DISPATCH_CLASSIFY_1",
                    "source_signal_id": "SIGNAL_CLASSIFY_1",
                    "order_queued_id": "ORDER_QUEUED_CLASSIFY_1",
                    "broker_order_no": "12345",
                },
                "next_stage": "CHEJAN_EVENT_NORMALIZE_REQUIRED",
                "chejan_called": False,
                "runtime_write": False,
                "queue_write": False,
                "lifecycle_created": False,
            },
            "issues": [],
            "warnings": [],
            "chejan_called": False,
            "runtime_write": False,
            "queue_write": False,
            "lifecycle_created": False,
        }

    def _policy(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "classification_enabled": True,
            "confidence_mode": "STANDARD",
        }
        result.update(overrides)
        return result

    def test_order_received_candidate_normal(self) -> None:
        result = preview_chejan_event_classification(self._event(), self._policy())

        self.assertEqual("CLASSIFICATION_READY", result["status"])
        self.assertEqual("ORDER_RECEIVED_CANDIDATE", result["candidate_event_type"])
        self.assertEqual("HIGH", result["confidence"])
        self.assertFalse(result["lifecycle_created"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["classification_preview"]["final_state_confirmed"])

    def test_partial_fill_candidate_normal(self) -> None:
        event = self._event(event_type="RAW_CHEJAN_EVENT")
        event["chejan_event_contract"]["raw_chejan_event"]["fid_values"]["913"] = "FILLED"
        event["chejan_event_contract"]["raw_chejan_event"]["fid_values"]["911"] = "3"
        event["chejan_event_contract"]["raw_chejan_event"]["fid_values"]["902"] = "7"

        result = preview_chejan_event_classification(event, self._policy())

        self.assertEqual("CLASSIFICATION_READY", result["status"])
        self.assertEqual("PARTIAL_FILL_CANDIDATE", result["candidate_event_type"])

    def test_full_fill_candidate_normal(self) -> None:
        event = self._event(event_type="RAW_CHEJAN_EVENT")
        event["chejan_event_contract"]["raw_chejan_event"]["fid_values"]["913"] = "FILLED"
        event["chejan_event_contract"]["raw_chejan_event"]["fid_values"]["911"] = "10"
        event["chejan_event_contract"]["raw_chejan_event"]["fid_values"]["902"] = "0"

        result = preview_chejan_event_classification(event, self._policy())

        self.assertEqual("CLASSIFICATION_READY", result["status"])
        self.assertEqual("FULL_FILL_CANDIDATE", result["candidate_event_type"])

    def test_rejected_and_cancelled_candidates(self) -> None:
        rejected = preview_chejan_event_classification(self._event(event_type="ORDER_REJECTED"), self._policy())
        cancelled = preview_chejan_event_classification(self._event(event_type="ORDER_CANCELED"), self._policy())

        self.assertEqual("ORDER_REJECTED_CANDIDATE", rejected["candidate_event_type"])
        self.assertEqual("ORDER_CANCELLED_CANDIDATE", cancelled["candidate_event_type"])

    def test_unknown_candidate_allowed(self) -> None:
        result = preview_chejan_event_classification(self._event(event_type="ORDER_UNKNOWN"), self._policy())

        self.assertEqual("CLASSIFICATION_READY", result["status"])
        self.assertEqual("UNKNOWN_CANDIDATE", result["candidate_event_type"])
        self.assertEqual("LOW", result["confidence"])

    def test_event_blocked_is_blocked(self) -> None:
        event = self._event()
        event["status"] = "BLOCKED"
        event["issues"] = ["blocked"]

        result = preview_chejan_event_classification(event, self._policy())

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("chejan_event_contract_result.status is BLOCKED", result["issues"])

    def test_event_invalid_is_invalid(self) -> None:
        event = self._event()
        event["status"] = "INVALID"
        event["issues"] = ["bad"]

        result = preview_chejan_event_classification(event, self._policy())

        self.assertEqual("INVALID", result["status"])
        self.assertIn("chejan_event_contract_result.status is INVALID", result["issues"])

    def test_classification_disabled_is_blocked(self) -> None:
        result = preview_chejan_event_classification(self._event(), self._policy(classification_enabled=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("classification_policy.classification_enabled is not true", result["issues"])

    def test_malformed_policy_is_invalid(self) -> None:
        empty_policy = preview_chejan_event_classification(self._event(), {})
        bad_mode = preview_chejan_event_classification(self._event(), self._policy(confidence_mode="BAD"))

        self.assertEqual("INVALID", empty_policy["status"])
        self.assertEqual("INVALID", bad_mode["status"])

    def test_malformed_input_is_invalid(self) -> None:
        self.assertEqual("INVALID", preview_chejan_event_classification(None, self._policy())["status"])
        event = self._event()
        event["chejan_event_contract"]["raw_chejan_event"] = {}
        self.assertEqual("INVALID", preview_chejan_event_classification(event, self._policy())["status"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        event = self._event()
        policy = self._policy()
        before = (deepcopy(event), deepcopy(policy))

        result = preview_chejan_event_classification(event, policy)
        result["classification_preview"]["identity"]["order_id"] = "MUTATED_ORDER"
        result["classification_preview"]["chejan_event_contract"]["identity"]["order_id"] = "MUTATED_CONTRACT"

        self.assertEqual(before, (event, policy))
        fresh = preview_chejan_event_classification(event, policy)
        self.assertEqual("ORDER_CLASSIFY_1", fresh["classification_preview"]["identity"]["order_id"])

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        result = preview_chejan_event_classification(self._event(), self._policy())

        self.assertEqual("CLASSIFICATION_READY", result["status"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
