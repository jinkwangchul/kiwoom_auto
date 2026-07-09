# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_atomic_apply_preview import build_runtime_atomic_apply_preview
from lifecycle_runtime_commit_executor_preview import build_runtime_commit_executor_preview
from lifecycle_runtime_state_apply_controller_preview import (
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_runtime_state_apply_controller_preview,
)


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


def _ready_atomic_apply_preview(**overrides: object) -> dict[str, object]:
    executor = build_runtime_commit_executor_preview(
        _ready_reconciliation(), {"generated_at": "2026-07-09 09:00:00"}
    )
    result = build_runtime_atomic_apply_preview(executor, {"generated_at": "2026-07-09 09:00:00"})
    result.update(overrides)
    return result


class LifecycleRuntimeStateApplyControllerPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_atomic_apply_preview_builds_controller_preview(self) -> None:
        result = build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_RUNTIME_STATE_APPLY_CONTROLLER_PREVIEW", result["preview_type"])

    def test_apply_controller_preview_is_built(self) -> None:
        result = build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview())

        controller = result["apply_controller_preview"]
        self.assertTrue(controller)
        self.assertEqual(STATUS_READY, controller["status"])
        self.assertTrue(controller["ready_to_apply"])
        self.assertEqual("", controller["blocked_reason"])
        self.assertEqual("", controller["invalid_reason"])
        self.assertTrue(controller["preview_only"])
        self.assertFalse(controller["apply_executed"])

    def test_apply_gate_preview_is_built(self) -> None:
        result = build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview())

        gate = result["apply_gate_preview"]
        self.assertEqual("READY_FOR_OPERATOR_APPROVAL", gate["gate_status"])
        self.assertTrue(gate["approval_required"])
        self.assertTrue(gate["approval_token_required"])
        self.assertTrue(gate["operator_review_required"])
        self.assertTrue(gate["preview_only"])

    def test_apply_lock_preview_is_built(self) -> None:
        result = build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview())

        lock = result["apply_lock_preview"]
        self.assertTrue(lock["lock_required"])
        self.assertTrue(lock["lock_key"])
        self.assertFalse(lock["lock_acquired"])
        self.assertTrue(lock["preview_only"])

    def test_apply_execution_order_preview_is_built(self) -> None:
        result = build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview())

        order = result["apply_execution_order_preview"]
        self.assertTrue(order["backup"])
        self.assertTrue(order["runtime_apply"])
        self.assertTrue(order["position_apply"])
        self.assertTrue(order["balance_apply"])
        self.assertTrue(order["verify"])
        self.assertTrue(order["rollback_on_failure"])
        self.assertTrue(order["preview_only"])
        self.assertFalse(order["apply_executed"])

    def test_controller_validation_ready_true(self) -> None:
        result = build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview())

        validation = result["controller_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["blocked"])
        self.assertFalse(validation["invalid"])
        self.assertFalse(validation["issues"])

    def test_invalid_atomic_apply_preview_is_invalid(self) -> None:
        invalid = build_runtime_state_apply_controller_preview(
            _ready_atomic_apply_preview(status="INVALID")
        )
        unsupported = build_runtime_state_apply_controller_preview(
            _ready_atomic_apply_preview(status="SOMETHING_ELSE")
        )

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertFalse(invalid["controller_validation"]["ready"])
        self.assertTrue(invalid["controller_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, unsupported["status"])
        self.assertFalse(unsupported["controller_validation"]["ready"])

    def test_blocked_atomic_apply_preview_is_blocked(self) -> None:
        result = build_runtime_state_apply_controller_preview(
            _ready_atomic_apply_preview(status="BLOCKED")
        )

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["controller_validation"]["ready"])
        self.assertTrue(result["controller_validation"]["blocked"])
        self.assertFalse(result["apply_controller_preview"]["ready_to_apply"])

    def test_lock_acquired_is_always_false(self) -> None:
        ready = build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview())
        invalid = build_runtime_state_apply_controller_preview(
            _ready_atomic_apply_preview(status="INVALID")
        )
        blocked = build_runtime_state_apply_controller_preview(
            _ready_atomic_apply_preview(status="BLOCKED")
        )

        self.assertFalse(ready["lock_acquired"])
        self.assertFalse(invalid["lock_acquired"])
        self.assertFalse(blocked["lock_acquired"])
        self.assertFalse(ready["apply_lock_preview"]["lock_acquired"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["lock_acquired"])
        self.assertFalse(result["apply_executed"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_protected_files_unchanged(self) -> None:
        build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview())
        build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview(status="INVALID"))
        build_runtime_state_apply_controller_preview(_ready_atomic_apply_preview(status="BLOCKED"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
