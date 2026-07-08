# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from lifecycle_commit_dry_run import dry_run_lifecycle_commit


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


class LifecycleCommitDryRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _commit_preview(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "LIFECYCLE_COMMIT_READY",
            "commit_contract": {
                "contract_type": "ORDER_LIFECYCLE_COMMIT_CONTRACT_PREVIEW",
                "contract_version": "preview-1",
                "preview_only": True,
                "lifecycle_write": False,
                "runtime_write": False,
                "queue_write": False,
                "candidate_lifecycle_event": "ORDER_RECEIVED",
                "evidence_id": "CHEJAN_EVIDENCE_DRY_RUN_1",
                "record_id": "SEND_ORDER_RECORD_DRY_RUN_1",
                "order_id": "ORDER_DRY_RUN_1",
                "dispatch_id": "DISPATCH_DRY_RUN_1",
                "source_signal_id": "SIGNAL_DRY_RUN_1",
                "order_queued_id": "ORDER_QUEUED_DRY_RUN_1",
                "target_name": "in_memory_lifecycle_preview",
                "lifecycle_store": "preview_lifecycle_store",
                "required_next_service": "ORDER_LIFECYCLE_COMMIT_SERVICE",
            },
            "commit_plan": {
                "plan_type": "ORDER_LIFECYCLE_COMMIT_PLAN_PREVIEW",
                "preview_only": True,
                "lifecycle_write": False,
                "runtime_write": False,
                "queue_write": False,
                "would_append_event": "ORDER_RECEIVED",
            },
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "lifecycle_write": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _store_snapshot(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "snapshot_valid": True,
            "lifecycle_store": "preview_lifecycle_store",
            "existing_transitions": [],
            "existing_events": [],
        }
        result.update(overrides)
        return result

    def _runtime_context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "lifecycle_runtime_enabled": True,
            "emergency_stop": False,
        }
        result.update(overrides)
        return result

    def test_lifecycle_dry_run_ready_normal(self) -> None:
        result = dry_run_lifecycle_commit(self._commit_preview(), self._store_snapshot(), self._runtime_context())

        self.assertEqual("LIFECYCLE_DRY_RUN_READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["lifecycle_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        dry_run = result["dry_run"]
        self.assertTrue(dry_run["dry_run_ready"])
        self.assertTrue(dry_run["duplicate_check_passed"])
        self.assertEqual("ORDER_RECEIVED", dry_run["candidate_lifecycle_event"])
        self.assertFalse(dry_run["would_write_lifecycle"])

    def test_commit_preview_blocked_is_dry_run_blocked(self) -> None:
        result = dry_run_lifecycle_commit(
            self._commit_preview(status="BLOCKED", issues=["blocked"]),
            self._store_snapshot(),
            self._runtime_context(),
        )

        self.assertEqual("LIFECYCLE_DRY_RUN_BLOCKED", result["status"])

    def test_commit_preview_invalid_is_invalid(self) -> None:
        result = dry_run_lifecycle_commit(
            self._commit_preview(status="INVALID", issues=["bad"]),
            self._store_snapshot(),
            self._runtime_context(),
        )

        self.assertEqual("INVALID", result["status"])

    def test_duplicate_transition_is_blocked(self) -> None:
        result = dry_run_lifecycle_commit(
            self._commit_preview(),
            self._store_snapshot(
                existing_transitions=[
                    {
                        "order_id": "ORDER_DRY_RUN_1",
                        "candidate_lifecycle_event": "ORDER_RECEIVED",
                    }
                ]
            ),
            self._runtime_context(),
        )

        self.assertEqual("LIFECYCLE_DRY_RUN_BLOCKED", result["status"])
        self.assertIn("duplicate lifecycle transition exists", result["issues"])
        self.assertFalse(result["dry_run"]["duplicate_check_passed"])

    def test_duplicate_evidence_id_is_blocked(self) -> None:
        result = dry_run_lifecycle_commit(
            self._commit_preview(),
            self._store_snapshot(existing_events=[{"evidence_id": "CHEJAN_EVIDENCE_DRY_RUN_1"}]),
            self._runtime_context(),
        )

        self.assertEqual("LIFECYCLE_DRY_RUN_BLOCKED", result["status"])
        self.assertIn("duplicate lifecycle transition exists", result["issues"])

    def test_lifecycle_runtime_disabled_is_blocked(self) -> None:
        result = dry_run_lifecycle_commit(
            self._commit_preview(),
            self._store_snapshot(),
            self._runtime_context(lifecycle_runtime_enabled=False),
        )

        self.assertEqual("LIFECYCLE_DRY_RUN_BLOCKED", result["status"])
        self.assertIn("lifecycle runtime disabled", result["issues"])

    def test_store_snapshot_malformed_is_invalid(self) -> None:
        missing_snapshot = dry_run_lifecycle_commit(self._commit_preview(), {}, self._runtime_context())
        invalid_snapshot = dry_run_lifecycle_commit(
            self._commit_preview(),
            self._store_snapshot(snapshot_valid=False),
            self._runtime_context(),
        )
        malformed_transitions = dry_run_lifecycle_commit(
            self._commit_preview(),
            self._store_snapshot(existing_transitions={}),
            self._runtime_context(),
        )

        self.assertEqual("INVALID", missing_snapshot["status"])
        self.assertEqual("INVALID", invalid_snapshot["status"])
        self.assertEqual("INVALID", malformed_transitions["status"])

    def test_runtime_context_malformed_is_invalid(self) -> None:
        missing_context = dry_run_lifecycle_commit(self._commit_preview(), self._store_snapshot(), {})
        missing_flag = dry_run_lifecycle_commit(
            self._commit_preview(),
            self._store_snapshot(),
            {"emergency_stop": False},
        )

        self.assertEqual("INVALID", missing_context["status"])
        self.assertEqual("INVALID", missing_flag["status"])

    def test_malformed_preview_is_invalid(self) -> None:
        self.assertEqual("INVALID", dry_run_lifecycle_commit(None, self._store_snapshot(), self._runtime_context())["status"])
        self.assertEqual(
            "INVALID",
            dry_run_lifecycle_commit(
                self._commit_preview(commit_contract={}),
                self._store_snapshot(),
                self._runtime_context(),
            )["status"],
        )
        self.assertEqual(
            "INVALID",
            dry_run_lifecycle_commit(
                self._commit_preview(preview_only=False),
                self._store_snapshot(),
                self._runtime_context(),
            )["status"],
        )

    def test_deepcopy_defends_external_mutation(self) -> None:
        preview = self._commit_preview()
        store = self._store_snapshot()
        context = self._runtime_context()
        before = (deepcopy(preview), deepcopy(store), deepcopy(context))

        result = dry_run_lifecycle_commit(preview, store, context)
        result["dry_run"]["commit_contract"]["order_id"] = "MUTATED_ORDER"
        result["dry_run"]["lifecycle_store_snapshot"]["existing_transitions"].append({"event": "MUTATED"})
        result["dry_run"]["lifecycle_runtime_context"]["lifecycle_runtime_enabled"] = False

        self.assertEqual(before, (preview, store, context))
        fresh = dry_run_lifecycle_commit(preview, store, context)
        self.assertEqual("ORDER_DRY_RUN_1", fresh["dry_run"]["commit_contract"]["order_id"])
        self.assertTrue(fresh["dry_run"]["lifecycle_runtime_context"]["lifecycle_runtime_enabled"])

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        result = dry_run_lifecycle_commit(self._commit_preview(), self._store_snapshot(), self._runtime_context())

        self.assertEqual("LIFECYCLE_DRY_RUN_READY", result["status"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
