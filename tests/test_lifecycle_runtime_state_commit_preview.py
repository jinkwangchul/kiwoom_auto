# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_state_commit_preview import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_runtime_state_commit_preview,
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


def _ready_file_writer_preview(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "preview_type": "LIFECYCLE_RUNTIME_FILE_WRITER_PREVIEW",
        "status": "FILE_WRITER_PREVIEW_READY",
        "preview_only": True,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "writer_executed": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "file_target_preview": {
            "runtime_targets": [{"target_name": "runtime", "target_path": "runtime/runtime_snapshot.json"}],
            "position_targets": [{"target_name": "position", "target_path": "runtime/position_view.json"}],
            "balance_targets": [{"target_name": "balance", "target_path": "runtime/balance_view.json"}],
            "audit_targets": [{"target_name": "audit", "target_path": "runtime/audit.log"}],
            "preview_only": True,
        },
        "write_candidate_preview": {
            "runtime_write_candidates": ["ORDER_1"],
            "position_write_candidates": ["POSITION_1"],
            "balance_write_candidates": ["BALANCE_1"],
            "audit_write_candidates": ["AUDIT_1"],
            "candidate_count": 4,
            "preview_only": True,
        },
        "backup_requirement_preview": {
            "backup_required": True,
            "backup_created": False,
            "backup_targets": [{"target": "runtime", "path": "runtime/runtime_snapshot.json"}],
            "preview_only": True,
        },
        "write_sequence_preview": {
            "sequence_type": "RUNTIME_FILE_WRITE_PREVIEW_SEQUENCE",
            "ordered_steps": [{"step_index": 1, "action": "VERIFY_TRANSACTION"}],
            "write_executed": False,
            "preview_only": True,
        },
        "writer_preflight_validation": {
            "ready": True,
            "blocked": False,
            "invalid": False,
            "issues": [],
            "warnings": [],
            "preview_only": True,
        },
        "final_writer_decision": {
            "approved": True,
            "blocked": False,
            "file_write_allowed": False,
            "file_write_called": False,
            "preview_only": True,
        },
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


class LifecycleRuntimeStateCommitPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_file_writer_preview_builds_state_commit_preview_ready(self) -> None:
        result = build_runtime_state_commit_preview(_ready_file_writer_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["commit_preflight_validation"]["ready"])
        self.assertTrue(result["final_commit_decision"]["approved"])

    def test_blocked_file_writer_preview_is_blocked(self) -> None:
        result = build_runtime_state_commit_preview(_ready_file_writer_preview(status="BLOCKED", issues=["blocked upstream"]))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertTrue(result["commit_preflight_validation"]["blocked"])
        self.assertFalse(result["final_commit_decision"]["approved"])

    def test_invalid_and_malformed_file_writer_preview_are_invalid(self) -> None:
        invalid = build_runtime_state_commit_preview(_ready_file_writer_preview(status="INVALID"))
        malformed = build_runtime_state_commit_preview({"status": "FILE_WRITER_PREVIEW_READY"})

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertTrue(invalid["commit_preflight_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, malformed["status"])
        self.assertTrue(malformed["commit_preflight_validation"]["invalid"])

    def test_commit_candidate_preview_shape(self) -> None:
        result = build_runtime_state_commit_preview(_ready_file_writer_preview())
        candidate = result["commit_candidate_preview"]

        self.assertEqual(["ORDER_1"], candidate["runtime_commit_candidates"])
        self.assertEqual(["POSITION_1"], candidate["position_commit_candidates"])
        self.assertEqual(["BALANCE_1"], candidate["balance_commit_candidates"])
        self.assertEqual(["AUDIT_1"], candidate["audit_commit_candidates"])
        self.assertEqual(4, candidate["candidate_count"])
        self.assertTrue(candidate["preview_only"])

    def test_commit_boundary_preview_shape(self) -> None:
        result = build_runtime_state_commit_preview(_ready_file_writer_preview())
        boundary = result["commit_boundary_preview"]

        self.assertEqual("RUNTIME_STATE_COMMIT_PREVIEW_BOUNDARY", boundary["boundary_type"])
        self.assertTrue(boundary["atomic"])
        self.assertTrue(boundary["all_or_nothing"])
        self.assertTrue(boundary["requires_backup"])
        self.assertTrue(boundary["requires_rollback"])
        self.assertTrue(boundary["preview_only"])

    def test_commit_token_preview_shape(self) -> None:
        result = build_runtime_state_commit_preview(_ready_file_writer_preview())
        token = result["commit_token_preview"]

        self.assertTrue(token["token_required"])
        self.assertFalse(token["token_issued"])
        self.assertFalse(token["token_consumed"])
        self.assertTrue(token["preview_only"])

    def test_post_commit_verification_preview_shape(self) -> None:
        result = build_runtime_state_commit_preview(_ready_file_writer_preview())
        verification = result["post_commit_verification_preview"]

        self.assertTrue(verification["verification_required"])
        self.assertFalse(verification["verification_executed"])
        self.assertTrue(verification["verification_targets"]["runtime_targets"])
        self.assertTrue(verification["verification_targets"]["position_targets"])
        self.assertTrue(verification["verification_targets"]["balance_targets"])
        self.assertTrue(verification["verification_targets"]["audit_targets"])
        self.assertTrue(verification["preview_only"])

    def test_commit_preflight_validation_shape(self) -> None:
        result = build_runtime_state_commit_preview(_ready_file_writer_preview())
        validation = result["commit_preflight_validation"]

        self.assertTrue(validation["ready"])
        self.assertFalse(validation["blocked"])
        self.assertFalse(validation["invalid"])
        self.assertFalse(validation["issues"])
        self.assertTrue(validation["preview_only"])

    def test_final_commit_decision_shape(self) -> None:
        result = build_runtime_state_commit_preview(_ready_file_writer_preview())
        decision = result["final_commit_decision"]

        self.assertTrue(decision["approved"])
        self.assertFalse(decision["commit_allowed"])
        self.assertFalse(decision["commit_executed"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_state_commit_preview(_ready_file_writer_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["commit_executed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["audit_write"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_input_is_not_mutated(self) -> None:
        file_writer = _ready_file_writer_preview()
        original = copy.deepcopy(file_writer)

        build_runtime_state_commit_preview(file_writer)

        self.assertEqual(original, file_writer)

    def test_protected_files_hash_unchanged(self) -> None:
        build_runtime_state_commit_preview(_ready_file_writer_preview())
        build_runtime_state_commit_preview(_ready_file_writer_preview(status="BLOCKED"))
        build_runtime_state_commit_preview(_ready_file_writer_preview(status="INVALID"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
