# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from lifecycle_commit_contract_preview import build_lifecycle_commit_contract_preview


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


class LifecycleCommitContractPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _approval(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "LIFECYCLE_APPROVED",
            "approval": {
                "approval_type": "LIFECYCLE_TRANSITION_APPROVAL_GATE",
                "approved": True,
                "lifecycle_write_allowed": True,
                "candidate_lifecycle_event": "ORDER_RECEIVED",
                "transition_preview": {
                    "preview_type": "CHEJAN_TO_LIFECYCLE_TRANSITION_PREVIEW",
                    "evidence_id": "CHEJAN_EVIDENCE_COMMIT_1",
                    "candidate_event_type": "ORDER_RECEIVED_CANDIDATE",
                    "candidate_lifecycle_event": "ORDER_RECEIVED",
                    "lifecycle_created": False,
                    "runtime_write": False,
                    "queue_write": False,
                },
                "identity": {
                    "record_id": "SEND_ORDER_RECORD_COMMIT_1",
                    "order_id": "ORDER_COMMIT_1",
                    "dispatch_id": "DISPATCH_COMMIT_1",
                    "source_signal_id": "SIGNAL_COMMIT_1",
                    "order_queued_id": "ORDER_QUEUED_COMMIT_1",
                },
                "lifecycle_created": False,
                "runtime_write": False,
                "queue_write": False,
                "next_stage": "ORDER_LIFECYCLE_WRITE_REQUIRED",
            },
            "issues": [],
            "warnings": [],
            "lifecycle_write_allowed": True,
            "lifecycle_created": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _target(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "target_valid": True,
            "target_name": "in_memory_lifecycle_preview",
            "lifecycle_store": "preview_lifecycle_store",
            "lifecycle_write_enabled": True,
        }
        result.update(overrides)
        return result

    def _snapshot(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "snapshot_valid": True,
            "order_id": "ORDER_COMMIT_1",
            "current_status": "SEND_ORDER_SENT",
            "existing_events": [],
        }
        result.update(overrides)
        return result

    def test_lifecycle_commit_ready_normal(self) -> None:
        result = build_lifecycle_commit_contract_preview(self._approval(), self._target(), self._snapshot())

        self.assertEqual("LIFECYCLE_COMMIT_READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["lifecycle_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        contract = result["commit_contract"]
        self.assertEqual("ORDER_LIFECYCLE_COMMIT_CONTRACT_PREVIEW", contract["contract_type"])
        self.assertEqual("ORDER_RECEIVED", contract["candidate_lifecycle_event"])
        self.assertEqual("ORDER_COMMIT_1", contract["order_id"])
        self.assertEqual("DISPATCH_COMMIT_1", contract["dispatch_id"])
        self.assertEqual("SIGNAL_COMMIT_1", contract["source_signal_id"])
        self.assertEqual("in_memory_lifecycle_preview", contract["target_name"])
        self.assertEqual("ORDER_LIFECYCLE_COMMIT_SERVICE", contract["required_next_service"])
        self.assertEqual("ORDER_LIFECYCLE_COMMIT_PLAN_PREVIEW", result["commit_plan"]["plan_type"])

    def test_approval_denied_is_blocked(self) -> None:
        result = build_lifecycle_commit_contract_preview(
            self._approval(status="DENIED", issues=["denied"], lifecycle_write_allowed=False),
            self._target(),
            self._snapshot(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["lifecycle_write"])

    def test_approval_invalid_is_invalid(self) -> None:
        result = build_lifecycle_commit_contract_preview(
            self._approval(status="INVALID", issues=["bad"], lifecycle_write_allowed=False),
            self._target(),
            self._snapshot(),
        )

        self.assertEqual("INVALID", result["status"])

    def test_target_malformed_is_invalid(self) -> None:
        missing_target = build_lifecycle_commit_contract_preview(self._approval(), {}, self._snapshot())
        invalid_target = build_lifecycle_commit_contract_preview(
            self._approval(),
            self._target(target_valid=False),
            self._snapshot(),
        )
        missing_store = build_lifecycle_commit_contract_preview(
            self._approval(),
            self._target(lifecycle_store=""),
            self._snapshot(),
        )

        self.assertEqual("INVALID", missing_target["status"])
        self.assertEqual("INVALID", invalid_target["status"])
        self.assertEqual("INVALID", missing_store["status"])

    def test_snapshot_malformed_is_invalid(self) -> None:
        missing_snapshot = build_lifecycle_commit_contract_preview(self._approval(), self._target(), {})
        invalid_snapshot = build_lifecycle_commit_contract_preview(
            self._approval(),
            self._target(),
            self._snapshot(snapshot_valid=False),
        )
        malformed_events = build_lifecycle_commit_contract_preview(
            self._approval(),
            self._target(),
            self._snapshot(existing_events={}),
        )

        self.assertEqual("INVALID", missing_snapshot["status"])
        self.assertEqual("INVALID", invalid_snapshot["status"])
        self.assertEqual("INVALID", malformed_events["status"])

    def test_commit_contract_required_fields(self) -> None:
        result = build_lifecycle_commit_contract_preview(self._approval(), self._target(), self._snapshot())
        contract = result["commit_contract"]

        for field in (
            "contract_type",
            "candidate_lifecycle_event",
            "evidence_id",
            "record_id",
            "order_id",
            "dispatch_id",
            "source_signal_id",
            "order_queued_id",
            "target_name",
            "lifecycle_store",
            "required_next_service",
        ):
            self.assertTrue(contract[field], field)

    def test_missing_required_field_is_invalid(self) -> None:
        approval = self._approval()
        approval["approval"]["identity"]["order_id"] = ""

        result = build_lifecycle_commit_contract_preview(approval, self._target(), self._snapshot())

        self.assertEqual("INVALID", result["status"])

    def test_snapshot_identity_mismatch_is_invalid(self) -> None:
        result = build_lifecycle_commit_contract_preview(
            self._approval(),
            self._target(),
            self._snapshot(order_id="OTHER_ORDER"),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("snapshot order_id does not match approval identity", result["issues"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        approval = self._approval()
        target = self._target()
        snapshot = self._snapshot()
        before = (deepcopy(approval), deepcopy(target), deepcopy(snapshot))

        result = build_lifecycle_commit_contract_preview(approval, target, snapshot)
        result["commit_contract"]["order_id"] = "MUTATED_ORDER"
        result["commit_plan"]["target_context"]["target_name"] = "mutated"
        result["commit_plan"]["current_lifecycle_snapshot"]["existing_events"].append({"event": "MUTATED"})

        self.assertEqual(before, (approval, target, snapshot))
        fresh = build_lifecycle_commit_contract_preview(approval, target, snapshot)
        self.assertEqual("ORDER_COMMIT_1", fresh["commit_contract"]["order_id"])
        self.assertEqual("in_memory_lifecycle_preview", fresh["commit_plan"]["target_context"]["target_name"])

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        result = build_lifecycle_commit_contract_preview(self._approval(), self._target(), self._snapshot())

        self.assertEqual("LIFECYCLE_COMMIT_READY", result["status"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
