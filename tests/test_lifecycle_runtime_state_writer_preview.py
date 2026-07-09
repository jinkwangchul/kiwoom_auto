# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_atomic_apply_preview import build_runtime_atomic_apply_preview
from lifecycle_runtime_commit_executor_preview import build_runtime_commit_executor_preview
from lifecycle_runtime_state_apply_controller_preview import build_runtime_state_apply_controller_preview
from lifecycle_runtime_state_writer_preview import (
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_runtime_state_writer_preview,
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


def _ready_controller_preview(**overrides: object) -> dict[str, object]:
    executor = build_runtime_commit_executor_preview(
        _ready_reconciliation(), {"generated_at": "2026-07-09 09:00:00"}
    )
    atomic = build_runtime_atomic_apply_preview(executor, {"generated_at": "2026-07-09 09:00:00"})
    result = build_runtime_state_apply_controller_preview(atomic, {"generated_at": "2026-07-09 09:00:00"})
    result.update(overrides)
    return result


class LifecycleRuntimeStateWriterPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_controller_preview_builds_writer_preview(self) -> None:
        result = build_runtime_state_writer_preview(_ready_controller_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_RUNTIME_STATE_WRITER_PREVIEW", result["preview_type"])

    def test_runtime_write_preview_is_built(self) -> None:
        result = build_runtime_state_writer_preview(_ready_controller_preview())

        runtime = result["runtime_write_preview"]
        self.assertTrue(runtime)
        self.assertTrue(runtime["runtime_payload"])
        self.assertEqual("RUNTIME_STATE_WRITE_PAYLOAD_PREVIEW", runtime["runtime_payload"]["payload_type"])
        self.assertFalse(runtime["runtime_payload"]["runtime_write"])
        self.assertTrue(runtime["runtime_targets"]["target_path"])
        self.assertFalse(runtime["runtime_summary"]["write_called"])

    def test_position_write_preview_is_built(self) -> None:
        result = build_runtime_state_writer_preview(_ready_controller_preview())

        position = result["position_write_preview"]
        self.assertTrue(position)
        self.assertTrue(position["position_payload"])
        self.assertEqual("POSITION_WRITE_PAYLOAD_PREVIEW", position["position_payload"]["payload_type"])
        self.assertFalse(position["position_payload"]["position_write"])
        self.assertTrue(position["position_targets"]["target_path"])
        self.assertFalse(position["position_summary"]["write_called"])

    def test_balance_write_preview_is_built(self) -> None:
        result = build_runtime_state_writer_preview(_ready_controller_preview())

        balance = result["balance_write_preview"]
        self.assertTrue(balance)
        self.assertTrue(balance["balance_payload"])
        self.assertEqual("BALANCE_WRITE_PAYLOAD_PREVIEW", balance["balance_payload"]["payload_type"])
        self.assertFalse(balance["balance_payload"]["balance_write"])
        self.assertTrue(balance["balance_targets"]["target_path"])
        self.assertFalse(balance["balance_summary"]["write_called"])

    def test_write_sequence_preview_is_built(self) -> None:
        result = build_runtime_state_writer_preview(_ready_controller_preview())

        sequence = result["write_sequence_preview"]
        self.assertTrue(sequence["runtime"])
        self.assertTrue(sequence["position"])
        self.assertTrue(sequence["balance"])
        self.assertTrue(sequence["verification"])
        self.assertTrue(sequence["preview_only"])
        self.assertFalse(sequence["writer_called"])

    def test_writer_validation_ready_true(self) -> None:
        result = build_runtime_state_writer_preview(_ready_controller_preview())

        validation = result["writer_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["blocked"])
        self.assertFalse(validation["invalid"])
        self.assertFalse(validation["issues"])

    def test_invalid_controller_preview_is_invalid(self) -> None:
        invalid = build_runtime_state_writer_preview(_ready_controller_preview(status="INVALID"))
        unsupported = build_runtime_state_writer_preview(_ready_controller_preview(status="SOMETHING_ELSE"))

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertFalse(invalid["writer_validation"]["ready"])
        self.assertTrue(invalid["writer_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, unsupported["status"])
        self.assertFalse(unsupported["writer_validation"]["ready"])

    def test_blocked_controller_preview_is_blocked(self) -> None:
        result = build_runtime_state_writer_preview(_ready_controller_preview(status="BLOCKED"))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["writer_validation"]["ready"])
        self.assertTrue(result["writer_validation"]["blocked"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_state_writer_preview(_ready_controller_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["writer_called"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_protected_files_unchanged(self) -> None:
        build_runtime_state_writer_preview(_ready_controller_preview())
        build_runtime_state_writer_preview(_ready_controller_preview(status="INVALID"))
        build_runtime_state_writer_preview(_ready_controller_preview(status="BLOCKED"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
