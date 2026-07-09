# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_atomic_apply_preview import build_runtime_atomic_apply_preview
from lifecycle_runtime_commit_executor_preview import build_runtime_commit_executor_preview
from lifecycle_runtime_state_apply_controller_preview import build_runtime_state_apply_controller_preview
from lifecycle_runtime_state_writer_preview import build_runtime_state_writer_preview
from lifecycle_runtime_state_validator_preview import (
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_runtime_state_validator_preview,
)
from lifecycle_runtime_apply_orchestrator_preview import (
    STATUS_READY as ORCHESTRATOR_READY,
    STATUS_BLOCKED as ORCHESTRATOR_BLOCKED,
    STATUS_INVALID as ORCHESTRATOR_INVALID,
    build_runtime_apply_orchestrator_preview,
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


def _ready_validator_preview(**overrides: object) -> dict[str, object]:
    executor = build_runtime_commit_executor_preview(
        _ready_reconciliation(), {"generated_at": "2026-07-09 09:00:00"}
    )
    atomic = build_runtime_atomic_apply_preview(executor, {"generated_at": "2026-07-09 09:00:00"})
    controller = build_runtime_state_apply_controller_preview(atomic, {"generated_at": "2026-07-09 09:00:00"})
    writer = build_runtime_state_writer_preview(controller, {"generated_at": "2026-07-09 09:00:00"})
    result = build_runtime_state_validator_preview(writer, {"generated_at": "2026-07-09 09:00:00"})
    result.update(overrides)
    return result


class LifecycleRuntimeApplyOrchestratorPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_validator_preview_builds_orchestrator_preview(self) -> None:
        result = build_runtime_apply_orchestrator_preview(_ready_validator_preview())

        self.assertEqual(ORCHESTRATOR_READY, result["status"])
        self.assertEqual("LIFECYCLE_RUNTIME_APPLY_ORCHESTRATOR_PREVIEW", result["preview_type"])

    def test_apply_orchestrator_preview_is_built(self) -> None:
        result = build_runtime_apply_orchestrator_preview(_ready_validator_preview())

        orchestrator = result["apply_orchestrator_preview"]
        self.assertTrue(orchestrator)
        self.assertTrue(orchestrator["orchestrator_id"])
        self.assertEqual(9, len(orchestrator["execution_pipeline"]))
        self.assertTrue(orchestrator["pipeline_summary"]["stage_count"])

    def test_pipeline_execution_preview_is_built(self) -> None:
        result = build_runtime_apply_orchestrator_preview(_ready_validator_preview())

        pipeline = result["pipeline_execution_preview"]
        self.assertTrue(pipeline)
        self.assertEqual(9, len(pipeline))
        self.assertTrue(pipeline.get("validator_stage"))

    def test_orchestrator_validation_ready_true(self) -> None:
        result = build_runtime_apply_orchestrator_preview(_ready_validator_preview())

        validation = result["orchestrator_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["blocked"])
        self.assertFalse(validation["invalid"])
        self.assertFalse(validation["issues"])

    def test_final_apply_decision_preview_approved_true(self) -> None:
        result = build_runtime_apply_orchestrator_preview(_ready_validator_preview())

        decision = result["final_apply_decision_preview"]
        self.assertTrue(decision["approved"])
        self.assertFalse(decision["blocked"])
        self.assertEqual("validator preview ready and full apply pipeline preview is ready", decision["approval_reason"])
        self.assertTrue(decision["preview_only"])
        self.assertFalse(decision["apply_executed"])

    def test_invalid_validator_preview_is_invalid(self) -> None:
        invalid = build_runtime_apply_orchestrator_preview(_ready_validator_preview(status="INVALID"))
        unsupported = build_runtime_apply_orchestrator_preview(_ready_validator_preview(status="SOMETHING_ELSE"))

        self.assertEqual(ORCHESTRATOR_INVALID, invalid["status"])
        self.assertFalse(invalid["orchestrator_validation"]["ready"])
        self.assertTrue(invalid["orchestrator_validation"]["invalid"])
        self.assertEqual(ORCHESTRATOR_INVALID, unsupported["status"])
        self.assertFalse(unsupported["orchestrator_validation"]["ready"])

    def test_blocked_validator_preview_is_blocked(self) -> None:
        result = build_runtime_apply_orchestrator_preview(_ready_validator_preview(status="BLOCKED"))

        self.assertEqual(ORCHESTRATOR_BLOCKED, result["status"])
        self.assertFalse(result["orchestrator_validation"]["ready"])
        self.assertTrue(result["orchestrator_validation"]["blocked"])
        self.assertFalse(result["final_apply_decision_preview"]["approved"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_apply_orchestrator_preview(_ready_validator_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["apply_executed"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_protected_files_unchanged(self) -> None:
        build_runtime_apply_orchestrator_preview(_ready_validator_preview())
        build_runtime_apply_orchestrator_preview(_ready_validator_preview(status="INVALID"))
        build_runtime_apply_orchestrator_preview(_ready_validator_preview(status="BLOCKED"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()