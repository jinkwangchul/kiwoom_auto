# -*- coding: utf-8 -*-
"""Tests for runtime_commit_executor (M6-6)."""

from __future__ import annotations

import copy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from runtime_commit_executor import (
    SAFETY_FLAG_NAMES,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    STEP_SEQUENCE,
    create_runtime_commit_execution_plan,
    create_runtime_commit_execution_plan_preview,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
RULES_PATH = PROJECT_ROOT / "routines" / "지표추종매매" / "rules.json"


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_hashes() -> dict[str, str | None]:
    hashes = {str(path): _sha256(path) for path in RUNTIME_DIR.glob("*.json")}
    hashes[str(RULES_PATH)] = _sha256(RULES_PATH)
    return hashes


class TestRuntimeCommitExecutorPreview(unittest.TestCase):
    def setUp(self) -> None:
        self.commit_id = "commit-executor-1"
        self.boundary = {
            "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
            "commit_id": self.commit_id,
            "preview_only": True,
            "issues": [],
            "warnings": [],
        }
        self.atomic = {
            "writer_status": "OK",
            "commit_id": self.commit_id,
            "preview_only": True,
            "issues": [],
            "warnings": [],
        }
        self.backup = {
            "backup_status": STATUS_READY,
            "commit_id": self.commit_id,
            "preview_only": True,
            "backup_targets": [{"source": "runtime/order_queue.json"}],
            "issues": [],
            "warnings": [],
        }
        self.rollback = {
            "rollback_status": STATUS_READY,
            "commit_id": self.commit_id,
            "preview_only": True,
            "rollback_targets": [{"source": "runtime/order_queue.json"}],
            "issues": [],
            "warnings": [],
        }
        self.verifier = {
            "verification_status": STATUS_READY,
            "commit_id": self.commit_id,
            "preview_only": True,
            "rollback_required": False,
            "issues": [],
            "warnings": [],
        }
        self.audit = {
            "audit_status": STATUS_READY,
            "commit_id": self.commit_id,
            "preview_only": True,
            "issues": [],
            "warnings": [],
        }
        self.protected_hashes = _protected_hashes()

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, _protected_hashes())

    def _build(self, **overrides: object) -> dict[str, object]:
        kwargs = {
            "commit_id": self.commit_id,
            "boundary_result": self.boundary,
            "atomic_writer_plan": self.atomic,
            "backup_plan": self.backup,
            "rollback_plan": None,
            "verifier_result": self.verifier,
            "audit_record": self.audit,
            "execution_context": None,
        }
        kwargs.update(overrides)
        return create_runtime_commit_execution_plan(**kwargs)

    def test_ready_plan_created(self) -> None:
        result = self._build()
        self.assertEqual(STATUS_READY, result["executor_status"])

    def test_alias_api_matches_existing_api(self) -> None:
        original = self._build()
        alias = create_runtime_commit_execution_plan_preview(
            commit_id=self.commit_id,
            boundary_result=self.boundary,
            atomic_writer_plan=self.atomic,
            backup_plan=self.backup,
            rollback_plan=None,
            verifier_result=self.verifier,
            audit_record=self.audit,
        )
        self.assertEqual(original["executor_status"], alias["executor_status"])
        self.assertEqual(original["plan_type"], alias["plan_type"])

    def test_execution_plan_preview_responsibility_fields(self) -> None:
        result = self._build()
        self.assertEqual("RUNTIME_COMMIT_EXECUTION_PLAN_PREVIEW", result["plan_type"])
        self.assertEqual("PRE_EXECUTION_PLAN", result["execution_phase"])
        self.assertFalse(result["execution_performed"])
        self.assertFalse(result["executable_without_real_gate"])
        self.assertFalse(result["actual_execution"])
        self.assertEqual("RUNTIME_COMMIT_EXECUTION_PLAN_PREVIEW", result["execution_plan"]["plan_type"])
        self.assertFalse(result["execution_plan"]["execution_performed"])
        self.assertFalse(result["execution_plan"]["executable_without_real_gate"])

    def test_ready_to_execute_means_gate_entry_available(self) -> None:
        result = self._build()
        self.assertEqual("READY_TO_EXECUTE", result["state_machine"]["terminal_state"])
        self.assertEqual("GATE_ENTRY_READY", result["state_machine"]["handoff_state"])
        self.assertEqual("Gate entry available", result["state_machine"]["state_semantics"])
        self.assertEqual("Gate entry available", result["execution_plan"]["state_semantics"])

    def test_preview_only_true(self) -> None:
        self.assertTrue(self._build()["preview_only"])

    def test_execution_mode_preview(self) -> None:
        self.assertEqual("PREVIEW", self._build()["execution_plan"]["execution_mode"])

    def test_executable_true_when_ready(self) -> None:
        self.assertTrue(self._build()["execution_plan"]["executable"])

    def test_actual_execution_performed_false(self) -> None:
        self.assertFalse(self._build()["execution_plan"]["actual_execution_performed"])

    def test_execution_steps_count(self) -> None:
        self.assertEqual(7, len(self._build()["execution_steps"]))

    def test_step_sequence_exact(self) -> None:
        result = self._build()
        self.assertEqual([step[0] for step in STEP_SEQUENCE], [s["step_name"] for s in result["execution_steps"]])

    def test_step_index_contiguous(self) -> None:
        self.assertEqual(list(range(1, 8)), [s["step_index"] for s in self._build()["execution_steps"]])

    def test_all_callable_invoked_false(self) -> None:
        for step in self._build()["execution_steps"]:
            self.assertFalse(step["callable_invoked"])

    def test_all_execution_performed_false(self) -> None:
        for step in self._build()["execution_steps"]:
            self.assertFalse(step["execution_performed"])

    def test_state_machine_created(self) -> None:
        self.assertIn("state_machine", self._build())

    def test_terminal_state_ready_to_execute(self) -> None:
        self.assertEqual("READY_TO_EXECUTE", self._build()["state_machine"]["terminal_state"])

    def test_current_state_ready_to_execute(self) -> None:
        self.assertEqual("READY_TO_EXECUTE", self._build()["state_machine"]["current_state"])

    def test_boundary_ready_recognized(self) -> None:
        self.assertEqual(STATUS_READY, self._build()["source_statuses"]["runtime_commit_boundary"])

    def test_boundary_alias_status_recognized(self) -> None:
        boundary = {"status": "RUNTIME_COMMIT_BOUNDARY_READY", "commit_id": self.commit_id}
        self.assertEqual(STATUS_READY, self._build(boundary_result=boundary)["executor_status"])

    def test_atomic_writer_ready_recognized(self) -> None:
        self.assertEqual(STATUS_READY, self._build()["source_statuses"]["runtime_atomic_writer"])

    def test_backup_ready_recognized(self) -> None:
        self.assertEqual(STATUS_READY, self._build()["source_statuses"]["runtime_backup_manager"])

    def test_verifier_verification_status_recognized(self) -> None:
        self.assertEqual(STATUS_READY, self._build()["source_statuses"]["runtime_commit_verifier"])

    def test_verifier_verify_status_alias_recognized(self) -> None:
        verifier = dict(self.verifier)
        verifier.pop("verification_status")
        verifier["verify_status"] = STATUS_READY
        self.assertEqual(STATUS_READY, self._build(verifier_result=verifier)["source_statuses"]["runtime_commit_verifier"])

    def test_audit_ready_recognized(self) -> None:
        self.assertEqual(STATUS_READY, self._build()["source_statuses"]["runtime_commit_audit_record"])

    def test_rollback_plan_ready_recognized(self) -> None:
        self.assertEqual(STATUS_READY, self._build(rollback_plan=self.rollback)["source_statuses"]["runtime_rollback_manager"])

    def test_rollback_required_false_normal(self) -> None:
        result = self._build()
        self.assertFalse(result["execution_plan"]["rollback_required"])

    def test_rollback_required_true_blocks(self) -> None:
        verifier = dict(self.verifier)
        verifier["rollback_required"] = True
        result = self._build(verifier_result=verifier)
        self.assertEqual(STATUS_BLOCKED, result["executor_status"])

    def test_boundary_blocked_blocks(self) -> None:
        boundary = dict(self.boundary)
        boundary["runtime_commit_boundary_status"] = "RUNTIME_COMMIT_BOUNDARY_BLOCKED"
        self.assertEqual(STATUS_BLOCKED, self._build(boundary_result=boundary)["executor_status"])

    def test_atomic_writer_blocked_blocks(self) -> None:
        atomic = dict(self.atomic)
        atomic["writer_status"] = "ERROR"
        self.assertEqual(STATUS_BLOCKED, self._build(atomic_writer_plan=atomic)["executor_status"])

    def test_backup_blocked_blocks(self) -> None:
        backup = dict(self.backup)
        backup["backup_status"] = STATUS_BLOCKED
        self.assertEqual(STATUS_BLOCKED, self._build(backup_plan=backup)["executor_status"])

    def test_verifier_blocked_blocks(self) -> None:
        verifier = dict(self.verifier)
        verifier["verification_status"] = STATUS_BLOCKED
        self.assertEqual(STATUS_BLOCKED, self._build(verifier_result=verifier)["executor_status"])

    def test_audit_blocked_blocks(self) -> None:
        audit = dict(self.audit)
        audit["audit_status"] = STATUS_BLOCKED
        self.assertEqual(STATUS_BLOCKED, self._build(audit_record=audit)["executor_status"])

    def test_source_issue_blocks(self) -> None:
        backup = dict(self.backup)
        backup["issues"] = ["source issue"]
        self.assertEqual(STATUS_BLOCKED, self._build(backup_plan=backup)["executor_status"])

    def test_missing_commit_id_invalid(self) -> None:
        self.assertEqual(STATUS_INVALID, self._build(commit_id="")["executor_status"])

    def test_missing_boundary_invalid(self) -> None:
        self.assertEqual(STATUS_INVALID, self._build(boundary_result=None)["executor_status"])

    def test_boundary_type_error_invalid(self) -> None:
        self.assertEqual(STATUS_INVALID, self._build(boundary_result="bad")["executor_status"])

    def test_optional_source_type_error_invalid(self) -> None:
        self.assertEqual(STATUS_INVALID, self._build(backup_plan="bad")["executor_status"])

    def test_source_commit_id_mismatch_invalid(self) -> None:
        backup = dict(self.backup)
        backup["commit_id"] = "other"
        self.assertEqual(STATUS_INVALID, self._build(backup_plan=backup)["executor_status"])

    def test_source_invalid_status_invalid(self) -> None:
        verifier = dict(self.verifier)
        verifier["verification_status"] = STATUS_INVALID
        self.assertEqual(STATUS_INVALID, self._build(verifier_result=verifier)["executor_status"])

    def test_rules_json_target_invalid(self) -> None:
        backup = copy.deepcopy(self.backup)
        backup["backup_targets"] = [{"source": "routines/지표추종매매/rules.json"}]
        self.assertEqual(STATUS_INVALID, self._build(backup_plan=backup)["executor_status"])

    def test_runtime_write_true_invalid(self) -> None:
        boundary = dict(self.boundary)
        boundary["safety_flags"] = {"runtime_write": True}
        self.assertEqual(STATUS_INVALID, self._build(boundary_result=boundary)["executor_status"])

    def test_atomic_write_executed_true_invalid(self) -> None:
        atomic = dict(self.atomic)
        atomic["atomic_write_executed"] = True
        self.assertEqual(STATUS_INVALID, self._build(atomic_writer_plan=atomic)["executor_status"])

    def test_backup_created_true_invalid(self) -> None:
        backup = dict(self.backup)
        backup["safety_flags"] = {"backup_created": True}
        self.assertEqual(STATUS_INVALID, self._build(backup_plan=backup)["executor_status"])

    def test_rollback_executed_true_invalid(self) -> None:
        rollback = dict(self.rollback)
        rollback["safety_flags"] = {"rollback_executed": True}
        self.assertEqual(STATUS_INVALID, self._build(rollback_plan=rollback)["executor_status"])

    def test_audit_file_written_true_invalid(self) -> None:
        audit = dict(self.audit)
        audit["audit_file_written"] = True
        self.assertEqual(STATUS_INVALID, self._build(audit_record=audit)["executor_status"])

    def test_send_order_called_true_invalid(self) -> None:
        boundary = dict(self.boundary)
        boundary["send_order_called"] = True
        self.assertEqual(STATUS_INVALID, self._build(boundary_result=boundary)["executor_status"])

    def test_output_safety_flags_all_false(self) -> None:
        result = self._build()
        self.assertEqual(set(SAFETY_FLAG_NAMES), set(result["safety_flags"].keys()))
        for value in result["safety_flags"].values():
            self.assertFalse(value)

    def test_no_file_created(self) -> None:
        before = set(PROJECT_ROOT.glob("*executor*"))
        self._build()
        after = set(PROJECT_ROOT.glob("*executor*"))
        self.assertEqual(before, after)

    def test_runtime_json_unchanged(self) -> None:
        before = _protected_hashes()
        self._build()
        self.assertEqual(before, _protected_hashes())

    def test_rules_json_unchanged(self) -> None:
        before = _sha256(RULES_PATH)
        self._build()
        self.assertEqual(before, _sha256(RULES_PATH))

    def test_other_m6_public_apis_not_called(self) -> None:
        with mock.patch("runtime_atomic_writer.write_json_atomic") as writer, mock.patch(
            "runtime_backup_manager.create_runtime_backup_plan"
        ) as backup, mock.patch(
            "runtime_rollback_manager.create_runtime_rollback_plan"
        ) as rollback, mock.patch(
            "runtime_commit_verifier.verify_runtime_commit"
        ) as verifier, mock.patch(
            "runtime_commit_audit_record.create_runtime_commit_audit_record"
        ) as audit:
            self._build()
        writer.assert_not_called()
        backup.assert_not_called()
        rollback.assert_not_called()
        verifier.assert_not_called()
        audit.assert_not_called()

    def test_monkeypatched_m6_api_would_fail_if_called(self) -> None:
        with mock.patch("runtime_commit_verifier.verify_runtime_commit", side_effect=AssertionError("called")):
            self.assertEqual(STATUS_READY, self._build()["executor_status"])

    def test_input_dicts_are_not_mutated(self) -> None:
        boundary = copy.deepcopy(self.boundary)
        atomic = copy.deepcopy(self.atomic)
        backup = copy.deepcopy(self.backup)
        verifier = copy.deepcopy(self.verifier)
        audit = copy.deepcopy(self.audit)
        originals = copy.deepcopy((boundary, atomic, backup, verifier, audit))
        create_runtime_commit_execution_plan(self.commit_id, boundary, atomic, backup, None, verifier, audit)
        self.assertEqual(originals, (boundary, atomic, backup, verifier, audit))

    def test_issues_deterministic_dedupe(self) -> None:
        backup = dict(self.backup)
        backup["issues"] = ["same", "same", "later"]
        result = self._build(backup_plan=backup)
        self.assertEqual(["runtime_backup_manager: same", "runtime_backup_manager: later"], result["issues"])

    def test_warnings_deterministic_dedupe(self) -> None:
        backup = dict(self.backup)
        backup["warnings"] = ["same", "same", "later"]
        result = self._build(backup_plan=backup)
        self.assertEqual(["runtime_backup_manager: same", "runtime_backup_manager: later"], result["warnings"])

    def test_same_input_structure_except_timestamp_consistent(self) -> None:
        first = self._build()
        second = self._build()
        first["execution_metadata"]["created_at_preview"] = "<ts>"
        second["execution_metadata"]["created_at_preview"] = "<ts>"
        self.assertEqual(first, second)

    def test_optional_rollback_step_without_plan_ready(self) -> None:
        result = self._build(rollback_plan=None)
        self.assertEqual(STATUS_READY, result["executor_status"])
        rollback_step = result["execution_steps"][4]
        self.assertFalse(rollback_step["required"])

    def test_required_source_missing_blocked(self) -> None:
        result = self._build(backup_plan=None)
        self.assertEqual(STATUS_BLOCKED, result["executor_status"])

    def test_malformed_status_blocks(self) -> None:
        backup = dict(self.backup)
        backup["backup_status"] = "WEIRD"
        self.assertEqual(STATUS_BLOCKED, self._build(backup_plan=backup)["executor_status"])


if __name__ == "__main__":
    unittest.main()
