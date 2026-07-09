# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_synchronizer_preview import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_runtime_synchronizer_preview,
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


def _ready_state_commit_preview(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "preview_type": "LIFECYCLE_RUNTIME_STATE_COMMIT_PREVIEW",
        "status": "STATE_COMMIT_PREVIEW_READY",
        "preview_only": True,
        "commit_executed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "commit_candidate_preview": {
            "runtime_commit_candidates": ["ORDER_1"],
            "position_commit_candidates": ["POSITION_1"],
            "balance_commit_candidates": ["BALANCE_1"],
            "audit_commit_candidates": ["AUDIT_1"],
            "candidate_count": 4,
            "preview_only": True,
        },
        "post_commit_verification_preview": {
            "verification_required": True,
            "verification_executed": False,
            "verification_targets": {
                "runtime_targets": [{"target_name": "runtime", "target_path": "runtime/runtime_snapshot.json"}],
                "position_targets": [{"target_name": "position", "target_path": "runtime/position_view.json"}],
                "balance_targets": [{"target_name": "balance", "target_path": "runtime/balance_view.json"}],
                "audit_targets": [{"target_name": "audit", "target_path": "runtime/audit.log"}],
            },
            "preview_only": True,
        },
        "commit_preflight_validation": {
            "ready": True,
            "blocked": False,
            "invalid": False,
            "issues": [],
            "warnings": [],
            "preview_only": True,
        },
        "final_commit_decision": {
            "approved": True,
            "blocked": False,
            "invalid": False,
            "commit_allowed": False,
            "commit_executed": False,
            "preview_only": True,
        },
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


class LifecycleRuntimeSynchronizerPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_state_commit_preview_builds_synchronizer_ready(self) -> None:
        result = build_runtime_synchronizer_preview(_ready_state_commit_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["sync_preflight_validation"]["ready"])
        self.assertTrue(result["final_sync_decision"]["approved"])

    def test_blocked_state_commit_preview_is_blocked(self) -> None:
        result = build_runtime_synchronizer_preview(_ready_state_commit_preview(status="BLOCKED", issues=["blocked upstream"]))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertTrue(result["sync_preflight_validation"]["blocked"])
        self.assertFalse(result["final_sync_decision"]["approved"])

    def test_invalid_and_malformed_state_commit_preview_are_invalid(self) -> None:
        invalid = build_runtime_synchronizer_preview(_ready_state_commit_preview(status="INVALID"))
        malformed = build_runtime_synchronizer_preview({"status": "STATE_COMMIT_PREVIEW_READY"})

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertTrue(invalid["sync_preflight_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, malformed["status"])
        self.assertTrue(malformed["sync_preflight_validation"]["invalid"])

    def test_sync_target_preview_shape(self) -> None:
        result = build_runtime_synchronizer_preview(_ready_state_commit_preview())
        targets = result["sync_target_preview"]

        self.assertTrue(targets["runtime_sync_targets"])
        self.assertTrue(targets["position_sync_targets"])
        self.assertTrue(targets["balance_sync_targets"])
        self.assertTrue(targets["audit_sync_targets"])
        self.assertTrue(targets["preview_only"])

    def test_consistency_check_preview_shape(self) -> None:
        result = build_runtime_synchronizer_preview(_ready_state_commit_preview())
        check = result["consistency_check_preview"]

        self.assertTrue(check["consistency_check_required"])
        self.assertFalse(check["consistency_check_executed"])
        self.assertTrue(check["check_targets"]["runtime"])
        self.assertTrue(check["expected_consistency_state"]["runtime_position_consistent"])
        self.assertTrue(check["preview_only"])

    def test_sync_plan_preview_shape(self) -> None:
        result = build_runtime_synchronizer_preview(_ready_state_commit_preview())
        plan = result["sync_plan_preview"]

        self.assertTrue(plan["runtime_sync_plan"])
        self.assertTrue(plan["position_sync_plan"])
        self.assertTrue(plan["balance_sync_plan"])
        self.assertTrue(plan["audit_sync_plan"])
        self.assertEqual(4, plan["plan_count"])
        self.assertTrue(plan["preview_only"])

    def test_sync_sequence_preview_shape(self) -> None:
        result = build_runtime_synchronizer_preview(_ready_state_commit_preview())
        sequence = result["sync_sequence_preview"]

        self.assertEqual("RUNTIME_SYNCHRONIZER_PREVIEW_SEQUENCE", sequence["sequence_type"])
        self.assertTrue(sequence["ordered_steps"])
        self.assertFalse(sequence["sync_executed"])
        self.assertTrue(sequence["preview_only"])

    def test_sync_preflight_validation_shape(self) -> None:
        result = build_runtime_synchronizer_preview(_ready_state_commit_preview())
        validation = result["sync_preflight_validation"]

        self.assertTrue(validation["ready"])
        self.assertFalse(validation["blocked"])
        self.assertFalse(validation["invalid"])
        self.assertFalse(validation["issues"])
        self.assertTrue(validation["preview_only"])

    def test_final_sync_decision_shape(self) -> None:
        result = build_runtime_synchronizer_preview(_ready_state_commit_preview())
        decision = result["final_sync_decision"]

        self.assertTrue(decision["approved"])
        self.assertFalse(decision["sync_allowed"])
        self.assertFalse(decision["sync_executed"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_synchronizer_preview(_ready_state_commit_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["sync_executed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["audit_write"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["commit_executed"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_input_is_not_mutated(self) -> None:
        state_commit = _ready_state_commit_preview()
        original = copy.deepcopy(state_commit)

        build_runtime_synchronizer_preview(state_commit)

        self.assertEqual(original, state_commit)

    def test_protected_files_hash_unchanged(self) -> None:
        build_runtime_synchronizer_preview(_ready_state_commit_preview())
        build_runtime_synchronizer_preview(_ready_state_commit_preview(status="BLOCKED"))
        build_runtime_synchronizer_preview(_ready_state_commit_preview(status="INVALID"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
