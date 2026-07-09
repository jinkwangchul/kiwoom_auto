# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_file_writer_preview import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_FILE_WRITER_READY,
    build_runtime_file_writer_preview,
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


def _ready_transaction_preview(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "TRANSACTION_PREVIEW_READY",
        "preview_only": True,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "runtime_write_candidates": ["ORDER_EXECUTOR_1"],
        "position_write_candidates": [],
        "balance_write_candidates": ["ORDER_EXECUTOR_1"],
        "audit_write_candidates": [],
        "transaction_targets": {
            "runtime_target_path": "runtime/runtime_snapshot.json",
            "position_target_path": "runtime/position_view.json",
            "balance_target_path": "runtime/balance_view.json",
            "audit_target_path": "runtime/audit.log",
        },
        "backup_options": {
            "backup_runtime": True,
            "backup_position": True,
            "backup_balance": True,
            "backup_audit": False,
        },
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


class LifecycleRuntimeFileWriterPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_transaction_preview_builds_file_writer_preview(self) -> None:
        result = build_runtime_file_writer_preview(_ready_transaction_preview())

        self.assertEqual(STATUS_FILE_WRITER_READY, result["status"])
        self.assertEqual("LIFECYCLE_RUNTIME_FILE_WRITER_PREVIEW", result["preview_type"])

    def test_blocked_transaction_preview_is_blocked(self) -> None:
        result = build_runtime_file_writer_preview(_ready_transaction_preview(status="BLOCKED"))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["writer_preflight_validation"]["ready"])
        self.assertTrue(result["writer_preflight_validation"]["blocked"])
        self.assertFalse(result["final_writer_decision"]["approved"])

    def test_invalid_transaction_preview_is_invalid(self) -> None:
        invalid = build_runtime_file_writer_preview(_ready_transaction_preview(status="INVALID"))
        unsupported = build_runtime_file_writer_preview(_ready_transaction_preview(status="SOMETHING_ELSE"))

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertFalse(invalid["writer_preflight_validation"]["ready"])
        self.assertTrue(invalid["writer_preflight_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, unsupported["status"])
        self.assertFalse(unsupported["writer_preflight_validation"]["ready"])

    def test_malformed_input_is_invalid(self) -> None:
        none_result = build_runtime_file_writer_preview(None)
        self.assertEqual(STATUS_INVALID, none_result["status"])

        empty_result = build_runtime_file_writer_preview({})
        self.assertEqual(STATUS_INVALID, empty_result["status"])

    def test_file_target_preview_is_built(self) -> None:
        result = build_runtime_file_writer_preview(_ready_transaction_preview())

        target = result["file_target_preview"]
        self.assertTrue(target)
        self.assertTrue(target.get("runtime_targets"))
        self.assertTrue(target.get("position_targets"))
        self.assertTrue(target.get("balance_targets"))
        self.assertTrue(target.get("audit_targets"))
        self.assertTrue(target["preview_only"])

    def test_write_candidate_preview_is_built(self) -> None:
        result = build_runtime_file_writer_preview(_ready_transaction_preview())

        candidate = result["write_candidate_preview"]
        self.assertTrue(candidate)
        self.assertIsInstance(candidate.get("runtime_write_candidates"), list)
        self.assertIsInstance(candidate.get("position_write_candidates"), list)
        self.assertIsInstance(candidate.get("balance_write_candidates"), list)
        self.assertIsInstance(candidate.get("audit_write_candidates"), list)
        self.assertEqual(2, candidate["candidate_count"])
        self.assertTrue(candidate["preview_only"])

    def test_backup_requirement_preview_is_built(self) -> None:
        result = build_runtime_file_writer_preview(_ready_transaction_preview())

        backup = result["backup_requirement_preview"]
        self.assertTrue(backup)
        self.assertTrue(backup["backup_required"])
        self.assertFalse(backup["backup_created"])
        self.assertTrue(backup.get("backup_targets"))
        self.assertTrue(backup["preview_only"])

    def test_write_sequence_preview_is_built(self) -> None:
        result = build_runtime_file_writer_preview(_ready_transaction_preview())

        sequence = result["write_sequence_preview"]
        self.assertTrue(sequence)
        self.assertEqual("RUNTIME_FILE_WRITE_PREVIEW_SEQUENCE", sequence["sequence_type"])
        self.assertTrue(sequence.get("ordered_steps"))
        self.assertFalse(sequence["write_executed"])
        self.assertTrue(sequence["preview_only"])

    def test_writer_preflight_validation_ready_true(self) -> None:
        result = build_runtime_file_writer_preview(_ready_transaction_preview())

        validation = result["writer_preflight_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["blocked"])
        self.assertFalse(validation["invalid"])
        self.assertFalse(validation["issues"])
        self.assertTrue(validation["preview_only"])

    def test_final_writer_decision_approved_true(self) -> None:
        result = build_runtime_file_writer_preview(_ready_transaction_preview())

        decision = result["final_writer_decision"]
        self.assertTrue(decision["approved"])
        self.assertFalse(decision["file_write_allowed"])
        self.assertFalse(decision["file_write_called"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_file_writer_preview(_ready_transaction_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["audit_write"])
        self.assertFalse(result["writer_executed"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_protected_files_unchanged(self) -> None:
        build_runtime_file_writer_preview(_ready_transaction_preview())
        build_runtime_file_writer_preview(_ready_transaction_preview(status="INVALID"))
        build_runtime_file_writer_preview(_ready_transaction_preview(status="BLOCKED"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()