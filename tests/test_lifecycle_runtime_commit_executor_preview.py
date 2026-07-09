# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_commit_executor_preview import build_runtime_commit_executor_preview


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


class LifecycleRuntimeCommitExecutorPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _ready_reconciliation(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "RECONCILIATION_PREVIEW_READY",
            "preview_only": True,
            "mismatch_candidates": [
                {
                    "mismatch_id": "MISMATCH_1",
                    "order_id": "ORDER_EXECUTOR_1",
                    "field": "quantity",
                    "runtime_value": 1,
                    "broker_value": 2,
                    "review_required": False,
                },
                {
                    "mismatch_id": "MISMATCH_2",
                    "order_id": "ORDER_EXECUTOR_1",
                    "field": "balance",
                    "runtime_value": 1000,
                    "broker_value": 900,
                    "review_required": False,
                },
            ],
            "reconciliation_actions": [
                {
                    "action_id": "ACTION_1",
                    "order_id": "ORDER_EXECUTOR_1",
                    "action_type": "APPLY_RECONCILIATION_PREVIEW",
                    "runtime_write": False,
                    "broker_write": False,
                    "reconciliation_executed": False,
                }
            ],
            "review_required_items": [],
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_ready_reconciliation_builds_executor_preview(self) -> None:
        result = build_runtime_commit_executor_preview(self._ready_reconciliation(), {"generated_at": "2026-07-09 09:00:00"})

        self.assertEqual("READY", result["status"])
        self.assertEqual("LIFECYCLE_RUNTIME_COMMIT_EXECUTOR_PREVIEW", result["preview_type"])

    def test_commit_execution_plan_executor_preview_and_atomic_plan_are_included(self) -> None:
        result = build_runtime_commit_executor_preview(self._ready_reconciliation())

        self.assertIn("commit_execution_plan", result)
        self.assertIn("executor_preview", result)
        self.assertIn("atomic_execution_plan", result)
        self.assertEqual(["ORDER_EXECUTOR_1"], result["commit_execution_plan"]["runtime_apply_order"])
        self.assertTrue(result["executor_preview"]["execution_steps"])
        self.assertTrue(result["atomic_execution_plan"]["transaction_groups"])

    def test_execution_validation_ready_true(self) -> None:
        result = build_runtime_commit_executor_preview(self._ready_reconciliation())

        self.assertTrue(result["execution_validation"]["ready"])
        self.assertFalse(result["execution_validation"]["blocked"])
        self.assertFalse(result["execution_validation"]["invalid"])

    def test_review_required_items_block_executor_preview(self) -> None:
        result = build_runtime_commit_executor_preview(
            self._ready_reconciliation(review_required_items=[{"order_id": "ORDER_EXECUTOR_1"}])
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["execution_validation"]["ready"])
        self.assertTrue(result["execution_validation"]["blocked"])
        self.assertIn("reconciliation has review_required_items", result["issues"][0])

    def test_non_preview_only_reconciliation_is_invalid(self) -> None:
        result = build_runtime_commit_executor_preview(self._ready_reconciliation(preview_only=False))

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["execution_validation"]["ready"])
        self.assertTrue(result["execution_validation"]["invalid"])

    def test_non_ready_reconciliation_status_is_invalid_or_blocked(self) -> None:
        invalid = build_runtime_commit_executor_preview(self._ready_reconciliation(status="INVALID"))
        blocked = build_runtime_commit_executor_preview(self._ready_reconciliation(status="BLOCKED"))
        unsupported = build_runtime_commit_executor_preview(self._ready_reconciliation(status="SOMETHING_ELSE"))

        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("INVALID", unsupported["status"])

    def test_preview_safety_flags_are_fixed(self) -> None:
        result = build_runtime_commit_executor_preview(self._ready_reconciliation())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["runtime_apply_called"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])


if __name__ == "__main__":
    unittest.main()
