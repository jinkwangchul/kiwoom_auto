# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_persistence import build_runtime_persistence_plan
from lifecycle_runtime_projection import project_lifecycle_commit_to_runtime_view


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


class LifecycleRuntimePersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _projection(self) -> dict[str, object]:
        commit_result = {
            "status": "COMMITTED",
            "commit_token": "TOKEN_PERSISTENCE_1",
            "commit_contract": {
                "candidate_lifecycle_event": "FULL_FILL",
                "order_id": "ORDER_PERSISTENCE_1",
                "dispatch_id": "DISPATCH_PERSISTENCE_1",
                "source_signal_id": "SIGNAL_PERSISTENCE_1",
                "order_queued_id": "QUEUED_PERSISTENCE_1",
                "evidence_id": "EVIDENCE_PERSISTENCE_1",
                "code": "005930",
                "account_no": "12345678",
                "side": "BUY",
                "quantity": 2,
                "price": 70000,
            },
        }
        snapshot = {"snapshot_valid": True, "orders": {}, "lifecycle_events": []}
        return project_lifecycle_commit_to_runtime_view(
            commit_result,
            snapshot,
            {"projected_at": "2026-07-08 09:00:00"},
        )

    def _context(self) -> dict[str, object]:
        return {
            "persistence_id": "PERSISTENCE_TEST_1",
            "planned_at": "2026-07-08 09:01:00",
            "runtime_targets": {"runtime_snapshot": "runtime/test_runtime_snapshot.json"},
            "position_targets": {"position_view": "runtime/test_position_view.json"},
            "balance_targets": {"balance_view": "runtime/test_balance_view.json"},
        }

    def test_projected_projection_builds_persistence_plan(self) -> None:
        result = build_runtime_persistence_plan(self._projection(), self._context())

        self.assertEqual("PERSISTENCE_PREVIEW_READY", result["status"])
        self.assertEqual("LIFECYCLE_RUNTIME_PERSISTENCE_PLAN_PREVIEW", result["persistence_plan"]["plan_type"])
        self.assertEqual("PERSISTENCE_TEST_1", result["persistence_summary"]["persistence_id"])
        self.assertEqual(3, result["runtime_write_plan"]["planned_write_count"])

    def test_non_projected_result_is_blocked_or_invalid(self) -> None:
        blocked = build_runtime_persistence_plan({"status": "BLOCKED", "issues": ["not committed"]}, self._context())
        invalid = build_runtime_persistence_plan({"status": "INVALID", "issues": ["bad projection"]}, self._context())

        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("INVALID", invalid["status"])
        self.assertFalse(blocked["validation_result"]["valid"])
        self.assertFalse(invalid["validation_result"]["valid"])

    def test_runtime_position_balance_targets_are_created(self) -> None:
        result = build_runtime_persistence_plan(self._projection(), self._context())

        self.assertEqual({"runtime_snapshot": "runtime/test_runtime_snapshot.json"}, result["runtime_targets"])
        self.assertEqual({"position_view": "runtime/test_position_view.json"}, result["position_targets"])
        self.assertEqual({"balance_view": "runtime/test_balance_view.json"}, result["balance_targets"])

    def test_backup_and_rollback_targets_are_created(self) -> None:
        result = build_runtime_persistence_plan(self._projection(), self._context())

        self.assertEqual("runtime/test_runtime_snapshot.json.bak", result["backup_targets"]["runtime_snapshot"])
        self.assertEqual("runtime/test_position_view.json.bak", result["backup_targets"]["position_view"])
        self.assertEqual("runtime/test_balance_view.json.bak", result["backup_targets"]["balance_view"])
        self.assertEqual("runtime/test_runtime_snapshot.json", result["rollback_targets"]["runtime_snapshot"])
        self.assertEqual("runtime/test_position_view.json", result["rollback_targets"]["position_view"])
        self.assertEqual("runtime/test_balance_view.json", result["rollback_targets"]["balance_view"])

    def test_validation_result_is_included(self) -> None:
        result = build_runtime_persistence_plan(self._projection(), self._context())

        self.assertIn("validation_result", result)
        self.assertTrue(result["validation_result"]["valid"])
        self.assertEqual("PERSISTENCE_PREVIEW_READY", result["validation_result"]["status"])

    def test_preview_only_and_no_write_or_external_call_flags_are_fixed(self) -> None:
        result = build_runtime_persistence_plan(self._projection(), self._context())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])


if __name__ == "__main__":
    unittest.main()
