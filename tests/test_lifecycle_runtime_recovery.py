# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_persistence import build_runtime_persistence_plan
from lifecycle_runtime_projection import project_lifecycle_commit_to_runtime_view
from lifecycle_runtime_recovery import build_runtime_recovery_preview


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


class LifecycleRuntimeRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _persistence_plan(self) -> dict[str, object]:
        projection = project_lifecycle_commit_to_runtime_view(
            {
                "status": "COMMITTED",
                "commit_token": "TOKEN_RECOVERY_1",
                "commit_contract": {
                    "candidate_lifecycle_event": "FULL_FILL",
                    "order_id": "ORDER_RECOVERY_1",
                    "dispatch_id": "DISPATCH_RECOVERY_1",
                    "source_signal_id": "SIGNAL_RECOVERY_1",
                    "order_queued_id": "QUEUED_RECOVERY_1",
                    "evidence_id": "EVIDENCE_RECOVERY_1",
                    "code": "005930",
                    "account_no": "12345678",
                    "side": "BUY",
                    "quantity": 1,
                    "price": 70000,
                },
            },
            {"snapshot_valid": True, "orders": {}, "lifecycle_events": []},
            {"projected_at": "2026-07-08 10:00:00"},
        )
        return build_runtime_persistence_plan(
            projection,
            {
                "persistence_id": "PERSISTENCE_RECOVERY_1",
                "planned_at": "2026-07-08 10:01:00",
                "runtime_targets": {"runtime_snapshot": "runtime/recovery_runtime_snapshot.json"},
                "position_targets": {"position_view": "runtime/recovery_position_view.json"},
                "balance_targets": {"balance_view": "runtime/recovery_balance_view.json"},
            },
        )

    def test_valid_persistence_plan_builds_recovery_preview(self) -> None:
        result = build_runtime_recovery_preview(self._persistence_plan(), {"planned_at": "2026-07-08 10:02:00"})

        self.assertEqual("RECOVERY_PREVIEW_READY", result["status"])
        self.assertEqual("LIFECYCLE_RUNTIME_RECOVERY_PREVIEW", result["recovery_type"])

    def test_recovery_candidates_are_created(self) -> None:
        result = build_runtime_recovery_preview(self._persistence_plan())

        self.assertEqual(3, len(result["recovery_candidates"]))
        target_keys = {candidate["target_key"] for candidate in result["recovery_candidates"]}
        self.assertEqual({"runtime_snapshot", "position_view", "balance_view"}, target_keys)

    def test_recovery_steps_are_created(self) -> None:
        result = build_runtime_recovery_preview(self._persistence_plan())

        self.assertEqual(3, len(result["recovery_steps"]))
        self.assertEqual("RESTORE_TARGET_PREVIEW", result["recovery_steps"][0]["step_type"])
        self.assertFalse(result["recovery_steps"][0]["recovery_executed"])

    def test_reconciliation_preview_and_summary_are_created(self) -> None:
        result = build_runtime_recovery_preview(self._persistence_plan())

        reconciliation = result["reconciliation_preview"]
        summary = result["recovery_summary"]
        self.assertEqual("LIFECYCLE_RUNTIME_RECONCILIATION_PREVIEW", reconciliation["preview_type"])
        self.assertEqual("PERSISTENCE_RECOVERY_1", reconciliation["persistence_id"])
        self.assertEqual(3, summary["recovery_candidate_count"])
        self.assertEqual(3, summary["recovery_step_count"])

    def test_validation_result_is_included(self) -> None:
        result = build_runtime_recovery_preview(self._persistence_plan())

        self.assertIn("validation_result", result)
        self.assertTrue(result["validation_result"]["valid"])
        self.assertEqual("RECOVERY_PREVIEW_READY", result["validation_result"]["status"])

    def test_invalid_or_blocked_persistence_plan_is_not_ready(self) -> None:
        invalid = build_runtime_recovery_preview({"status": "INVALID", "preview_only": True, "runtime_write": False})
        blocked = build_runtime_recovery_preview({"status": "BLOCKED", "preview_only": True, "runtime_write": False})

        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertFalse(invalid["validation_result"]["valid"])
        self.assertFalse(blocked["validation_result"]["valid"])

    def test_preview_only_and_no_write_or_external_call_flags_are_fixed(self) -> None:
        result = build_runtime_recovery_preview(self._persistence_plan())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["recovery_executed"])
        self.assertFalse(result["runtime_restored"])
        self.assertFalse(result["snapshot_loaded"])
        self.assertFalse(result["reconciliation_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])


if __name__ == "__main__":
    unittest.main()
