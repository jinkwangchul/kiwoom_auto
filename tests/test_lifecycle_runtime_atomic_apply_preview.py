# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_atomic_apply_preview import (
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_runtime_atomic_apply_preview,
)
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


def _ready_reconciliation(**overrides: object) -> dict[str, object]:
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


def _ready_executor_preview(**overrides: object) -> dict[str, object]:
    result = build_runtime_commit_executor_preview(
        _ready_reconciliation(), {"generated_at": "2026-07-09 09:00:00"}
    )
    result.update(overrides)
    return result


class LifecycleRuntimeAtomicApplyPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_executor_preview_builds_atomic_apply_preview(self) -> None:
        result = build_runtime_atomic_apply_preview(_ready_executor_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_RUNTIME_ATOMIC_APPLY_PREVIEW", result["preview_type"])

    def test_apply_batch_is_built(self) -> None:
        result = build_runtime_atomic_apply_preview(_ready_executor_preview())

        apply_batch = result["apply_batch"]
        self.assertTrue(apply_batch)
        self.assertTrue(apply_batch["batch_id"].startswith("ATOMIC_APPLY_BATCH_"))
        self.assertEqual("RUNTIME_POSITION_BALANCE_ATOMIC_APPLY_PREVIEW", apply_batch["batch_type"])
        self.assertTrue(apply_batch["transaction_groups"])
        self.assertTrue(apply_batch["commit_sequence"])
        self.assertTrue(apply_batch["executor_preview"])
        self.assertTrue(apply_batch["preview_only"])
        self.assertFalse(apply_batch["atomic_apply_executed"])

    def test_atomic_boundary_validation(self) -> None:
        result = build_runtime_atomic_apply_preview(_ready_executor_preview())

        boundary = result["atomic_boundary_validation"]
        self.assertEqual("ATOMIC_BOUNDARY_VALIDATION", boundary["validation_type"])
        self.assertTrue(boundary["ready"])
        self.assertEqual(3, boundary["group_count"])
        self.assertFalse(boundary["issues"])
        boundary_detail = boundary["boundary"]
        self.assertEqual("ALL_OR_NOTHING", boundary_detail["boundary_type"])
        self.assertTrue(boundary_detail["requires_backup_before_apply"])
        self.assertTrue(boundary_detail["requires_rollback_on_failure"])

    def test_pre_apply_validation(self) -> None:
        result = build_runtime_atomic_apply_preview(_ready_executor_preview())

        pre = result["pre_apply_validation"]
        self.assertEqual("PRE_ATOMIC_APPLY_VALIDATION", pre["validation_type"])
        self.assertTrue(pre["ready"])
        self.assertFalse(pre["issues"])
        self.assertTrue(pre["preview_only"])

    def test_post_apply_verification_preview(self) -> None:
        result = build_runtime_atomic_apply_preview(_ready_executor_preview())

        post = result["post_apply_verification_preview"]
        self.assertEqual("POST_ATOMIC_APPLY_VERIFICATION_PREVIEW", post["verification_type"])
        self.assertTrue(post["preview_only"])
        self.assertFalse(post["atomic_apply_executed"])
        self.assertEqual(3, post["expected_group_count"])
        self.assertFalse(post["verified"])
        self.assertIn("verify all-or-nothing boundary", post["checks"])

    def test_rollback_trigger_preview(self) -> None:
        result = build_runtime_atomic_apply_preview(_ready_executor_preview())

        rollback = result["rollback_trigger_preview"]
        self.assertEqual("ATOMIC_APPLY_ROLLBACK_TRIGGER_PREVIEW", rollback["trigger_type"])
        self.assertTrue(rollback["preview_only"])
        self.assertFalse(rollback["rollback_executed"])
        self.assertTrue(rollback["trigger_on_failure"])
        self.assertTrue(rollback["batch_id"].startswith("ATOMIC_APPLY_BATCH_"))
        self.assertIn("post-apply verification failure", rollback["rollback_reasons"])

    def test_non_ready_executor_preview_is_blocked_or_invalid(self) -> None:
        blocked = build_runtime_atomic_apply_preview(_ready_executor_preview(status="BLOCKED"))
        invalid = build_runtime_atomic_apply_preview(_ready_executor_preview(status="INVALID"))
        unsupported = build_runtime_atomic_apply_preview(_ready_executor_preview(status="SOMETHING_ELSE"))

        self.assertEqual(STATUS_BLOCKED, blocked["status"])
        self.assertFalse(blocked["pre_apply_validation"]["ready"])
        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertFalse(invalid["pre_apply_validation"]["ready"])
        self.assertEqual(STATUS_INVALID, unsupported["status"])
        self.assertFalse(unsupported["pre_apply_validation"]["ready"])

    def test_non_preview_only_executor_is_invalid(self) -> None:
        result = build_runtime_atomic_apply_preview(_ready_executor_preview(preview_only=False))

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertFalse(result["pre_apply_validation"]["ready"])
        self.assertIn("executor preview_only must be true", result["pre_apply_validation"]["issues"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_atomic_apply_preview(_ready_executor_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["atomic_apply_executed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_protected_files_unchanged(self) -> None:
        build_runtime_atomic_apply_preview(_ready_executor_preview())
        build_runtime_atomic_apply_preview(_ready_executor_preview(status="BLOCKED"))
        build_runtime_atomic_apply_preview(_ready_executor_preview(preview_only=False))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
