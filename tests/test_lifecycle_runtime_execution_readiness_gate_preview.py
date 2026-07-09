# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_execution_readiness_gate_preview import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_runtime_execution_readiness_gate_preview,
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


def _ready_synchronizer_preview(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "preview_type": "LIFECYCLE_RUNTIME_SYNCHRONIZER_PREVIEW",
        "status": "SYNCHRONIZER_PREVIEW_READY",
        "preview_only": True,
        "sync_executed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "commit_executed": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "sync_target_preview": {
            "runtime_sync_targets": [{"target_name": "runtime"}],
            "position_sync_targets": [{"target_name": "position"}],
            "balance_sync_targets": [{"target_name": "balance"}],
            "audit_sync_targets": [{"target_name": "audit"}],
            "preview_only": True,
        },
        "consistency_check_preview": {
            "consistency_check_required": True,
            "consistency_check_executed": False,
            "check_targets": {"runtime": [{"target_name": "runtime"}]},
            "expected_consistency_state": {
                "runtime_position_consistent": True,
                "runtime_balance_consistent": True,
                "audit_matches_runtime": True,
                "preview_only": True,
            },
            "preview_only": True,
        },
        "sync_plan_preview": {
            "runtime_sync_plan": {"sync_executed": False},
            "position_sync_plan": {"sync_executed": False},
            "balance_sync_plan": {"sync_executed": False},
            "audit_sync_plan": {"sync_executed": False},
            "plan_count": 4,
            "preview_only": True,
        },
        "sync_sequence_preview": {
            "sequence_type": "RUNTIME_SYNCHRONIZER_PREVIEW_SEQUENCE",
            "ordered_steps": [{"step_index": 1, "action": "VERIFY"}],
            "sync_executed": False,
            "preview_only": True,
        },
        "sync_preflight_validation": {
            "ready": True,
            "blocked": False,
            "invalid": False,
            "issues": [],
            "warnings": [],
            "preview_only": True,
        },
        "final_sync_decision": {
            "approved": True,
            "blocked": False,
            "invalid": False,
            "sync_allowed": False,
            "sync_executed": False,
            "preview_only": True,
        },
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


class LifecycleRuntimeExecutionReadinessGatePreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_synchronizer_preview_builds_readiness_gate_ready(self) -> None:
        result = build_runtime_execution_readiness_gate_preview(_ready_synchronizer_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["readiness_check_preview"]["ready_for_execution_layer"])
        self.assertTrue(result["final_readiness_decision"]["approved"])

    def test_blocked_synchronizer_preview_is_blocked(self) -> None:
        result = build_runtime_execution_readiness_gate_preview(
            _ready_synchronizer_preview(status="BLOCKED", issues=["blocked upstream"])
        )

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertTrue(result["blocking_reason_preview"]["blocked"])
        self.assertFalse(result["final_readiness_decision"]["approved"])

    def test_invalid_and_malformed_synchronizer_preview_are_invalid(self) -> None:
        invalid = build_runtime_execution_readiness_gate_preview(_ready_synchronizer_preview(status="INVALID"))
        malformed = build_runtime_execution_readiness_gate_preview({"status": "SYNCHRONIZER_PREVIEW_READY"})

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertTrue(invalid["blocking_reason_preview"]["invalid"])
        self.assertEqual(STATUS_INVALID, malformed["status"])
        self.assertTrue(malformed["blocking_reason_preview"]["invalid"])

    def test_readiness_check_preview_shape(self) -> None:
        result = build_runtime_execution_readiness_gate_preview(_ready_synchronizer_preview())
        readiness = result["readiness_check_preview"]

        self.assertTrue(readiness["readiness_required"])
        self.assertFalse(readiness["readiness_checked"])
        self.assertTrue(readiness["sync_preflight_ready"])
        self.assertTrue(readiness["final_sync_approved"])
        self.assertTrue(readiness["preview_only"])

    def test_execution_gate_preview_shape(self) -> None:
        result = build_runtime_execution_readiness_gate_preview(_ready_synchronizer_preview())
        gate = result["execution_gate_preview"]

        self.assertEqual("RUNTIME_EXECUTION_READINESS_GATE", gate["gate_type"])
        self.assertFalse(gate["execution_allowed"])
        self.assertFalse(gate["execution_started"])
        self.assertTrue(gate["manual_execution_approval_required"])
        self.assertTrue(gate["runtime_state_ready"])
        self.assertTrue(gate["preview_only"])

    def test_approval_requirement_preview_shape(self) -> None:
        result = build_runtime_execution_readiness_gate_preview(_ready_synchronizer_preview())
        approval = result["approval_requirement_preview"]

        self.assertTrue(approval["operator_approval_required"])
        self.assertTrue(approval["runtime_review_required"])
        self.assertTrue(approval["execution_token_required"])
        self.assertFalse(approval["approval_token_issued"])
        self.assertFalse(approval["approval_token_consumed"])
        self.assertTrue(approval["preview_only"])

    def test_blocking_reason_preview_shape(self) -> None:
        result = build_runtime_execution_readiness_gate_preview(
            _ready_synchronizer_preview(status="BLOCKED", issues=["blocked upstream"])
        )
        blocking = result["blocking_reason_preview"]

        self.assertTrue(blocking["blocked"])
        self.assertFalse(blocking["invalid"])
        self.assertIn("synchronizer preview is BLOCKED", blocking["blocking_reasons"])
        self.assertTrue(blocking["preview_only"])

    def test_final_readiness_decision_shape(self) -> None:
        result = build_runtime_execution_readiness_gate_preview(_ready_synchronizer_preview())
        decision = result["final_readiness_decision"]

        self.assertTrue(decision["approved"])
        self.assertFalse(decision["execution_allowed"])
        self.assertFalse(decision["execution_started"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_execution_readiness_gate_preview(_ready_synchronizer_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["audit_write"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["commit_executed"])
        self.assertFalse(result["sync_executed"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_input_is_not_mutated(self) -> None:
        synchronizer = _ready_synchronizer_preview()
        original = copy.deepcopy(synchronizer)

        build_runtime_execution_readiness_gate_preview(synchronizer)

        self.assertEqual(original, synchronizer)

    def test_protected_files_hash_unchanged(self) -> None:
        build_runtime_execution_readiness_gate_preview(_ready_synchronizer_preview())
        build_runtime_execution_readiness_gate_preview(_ready_synchronizer_preview(status="BLOCKED"))
        build_runtime_execution_readiness_gate_preview(_ready_synchronizer_preview(status="INVALID"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
