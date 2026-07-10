# -*- coding: utf-8 -*-
"""Tests for runtime_commit_execution_gate (M6-7)."""

from __future__ import annotations

import copy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from runtime_commit_execution_gate import (
    EXPECTED_STEP_SEQUENCE,
    SAFETY_FLAG_NAMES,
    STATUS_APPROVED,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_execution_plan_hash,
    evaluate_runtime_commit_execution_gate,
    evaluate_runtime_commit_execution_gate_preview,
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


class TestRuntimeCommitExecutionGate(unittest.TestCase):
    def setUp(self) -> None:
        self.commit_id = "commit-gate-1"
        self.plan = {
            "executor_status": "READY",
            "preview_only": True,
            "commit_id": self.commit_id,
            "execution_plan": {
                "commit_id": self.commit_id,
                "execution_mode": "PREVIEW",
                "executable": True,
                "rollback_required": False,
                "protected_target_violation": False,
                "actual_execution_performed": False,
            },
            "execution_steps": [
                {
                    "step_index": index,
                    "step_name": name,
                    "component": component,
                    "required": required,
                    "callable_invoked": False,
                    "execution_performed": False,
                    "issues": [],
                    "warnings": [],
                }
                for index, (name, component, required) in enumerate(
                    [
                        ("VALIDATE_BOUNDARY", "runtime_commit_boundary", True),
                        ("PREPARE_BACKUP", "runtime_backup_manager", True),
                        ("PREPARE_ATOMIC_WRITE", "runtime_atomic_writer", True),
                        ("VERIFY_COMMIT", "runtime_commit_verifier", True),
                        ("EVALUATE_ROLLBACK", "runtime_rollback_manager", False),
                        ("BUILD_AUDIT_RECORD", "runtime_commit_audit_record", True),
                        ("COMPLETE", "runtime_commit_executor", True),
                    ],
                    start=1,
                )
            ],
            "state_machine": {"current_state": "READY_TO_EXECUTE", "terminal_state": "READY_TO_EXECUTE"},
            "source_statuses": {"runtime_commit_boundary": "READY"},
            "issues": [],
            "warnings": [],
            "safety_flags": {},
        }
        self.plan_hash = build_execution_plan_hash(self.plan)
        self.approval = {
            "approved": True,
            "approved_commit_id": self.commit_id,
            "approved_plan_hash": self.plan_hash,
            "approved_by": "operator",
            "approval_reason": "manual approval",
            "approval_scope": "RUNTIME_COMMIT",
            "approval_timestamp": "2026-07-10 09:00:00",
            "single_use": True,
        }
        self.token = {
            "token_id": "token-1",
            "commit_id": self.commit_id,
            "plan_hash": self.plan_hash,
            "scope": "RUNTIME_COMMIT_EXECUTION",
            "issued_for": "operator",
            "single_use": True,
            "consumed": False,
        }
        self.protected_hashes = _protected_hashes()

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, _protected_hashes())

    def _gate(self, **overrides: object) -> dict[str, object]:
        kwargs = {
            "commit_id": self.commit_id,
            "execution_plan": self.plan,
            "approval_context": self.approval,
            "execution_token": self.token,
            "expected_plan_hash": self.plan_hash,
            "operator_context": None,
        }
        kwargs.update(overrides)
        return evaluate_runtime_commit_execution_gate(**kwargs)

    def test_valid_input_approved(self) -> None:
        self.assertEqual(STATUS_APPROVED, self._gate()["gate_status"])

    def test_alias_api_matches_existing_api(self) -> None:
        original = self._gate()
        alias = evaluate_runtime_commit_execution_gate_preview(
            commit_id=self.commit_id,
            execution_plan=self.plan,
            approval_context=self.approval,
            execution_token=self.token,
            expected_plan_hash=self.plan_hash,
        )
        self.assertEqual(original["gate_status"], alias["gate_status"])
        self.assertEqual(original["gate_type"], alias["gate_type"])

    def test_gate_preview_responsibility_fields(self) -> None:
        result = self._gate()
        self.assertEqual("RUNTIME_COMMIT_EXECUTION_GATE_PREVIEW", result["gate_type"])
        self.assertEqual("PRE_REAL_EXECUTION_VALIDATION", result["gate_phase"])
        self.assertTrue(result["approval_validation_only"])
        self.assertFalse(result["real_gate_active"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["actual_execution"])
        self.assertFalse(result["token_consumed"])
        self.assertFalse(result["token_persisted"])
        self.assertFalse(result["commit_lock_acquired"])
        self.assertFalse(result["replay_protection_active"])

    def test_gate_status_confirmed(self) -> None:
        self.assertEqual(STATUS_APPROVED, self._gate()["gate_status"])

    def test_execution_allowed_false(self) -> None:
        self.assertFalse(self._gate()["execution_allowed"])

    def test_ready_for_real_executor_true(self) -> None:
        self.assertTrue(self._gate()["ready_for_real_executor"])

    def test_preview_only_true(self) -> None:
        self.assertTrue(self._gate()["preview_only"])

    def test_plan_hash_deterministic(self) -> None:
        self.assertEqual(self.plan_hash, build_execution_plan_hash(copy.deepcopy(self.plan)))

    def test_same_input_same_hash(self) -> None:
        self.assertEqual(build_execution_plan_hash(self.plan), build_execution_plan_hash(copy.deepcopy(self.plan)))

    def test_execution_steps_change_changes_hash(self) -> None:
        changed = copy.deepcopy(self.plan)
        changed["execution_steps"][0]["step_name"] = "CHANGED"
        self.assertNotEqual(self.plan_hash, build_execution_plan_hash(changed))

    def test_metadata_timestamp_change_keeps_hash(self) -> None:
        changed = copy.deepcopy(self.plan)
        changed["execution_metadata"] = {"created_at_preview": "different"}
        self.assertEqual(self.plan_hash, build_execution_plan_hash(changed))

    def test_missing_commit_id_invalid(self) -> None:
        self.assertEqual(STATUS_INVALID, self._gate(commit_id="")["gate_status"])

    def test_execution_plan_type_error_invalid(self) -> None:
        self.assertEqual(STATUS_INVALID, self._gate(execution_plan="bad")["gate_status"])

    def test_approval_context_type_error_invalid(self) -> None:
        self.assertEqual(STATUS_INVALID, self._gate(approval_context="bad")["gate_status"])

    def test_execution_token_type_error_invalid(self) -> None:
        self.assertEqual(STATUS_INVALID, self._gate(execution_token="bad")["gate_status"])

    def test_execution_plan_commit_id_mismatch_invalid(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["commit_id"] = "other"
        self.assertEqual(STATUS_INVALID, self._gate(execution_plan=plan)["gate_status"])

    def test_approval_commit_id_mismatch_invalid(self) -> None:
        approval = dict(self.approval)
        approval["approved_commit_id"] = "other"
        self.assertEqual(STATUS_INVALID, self._gate(approval_context=approval)["gate_status"])

    def test_token_commit_id_mismatch_invalid(self) -> None:
        token = dict(self.token)
        token["commit_id"] = "other"
        self.assertEqual(STATUS_INVALID, self._gate(execution_token=token)["gate_status"])

    def test_executor_status_blocked_blocks(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["executor_status"] = "BLOCKED"
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_BLOCKED, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_executor_status_invalid_invalidates(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["executor_status"] = "INVALID"
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_INVALID, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_final_state_error_blocks(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["state_machine"]["terminal_state"] = "BLOCKED"
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_BLOCKED, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_required_step_missing_blocked(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["execution_steps"] = plan["execution_steps"][:-1]
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_BLOCKED, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_step_order_modified_invalid(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["execution_steps"][0], plan["execution_steps"][1] = plan["execution_steps"][1], plan["execution_steps"][0]
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_INVALID, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_duplicate_step_invalid(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["execution_steps"][1]["step_name"] = plan["execution_steps"][0]["step_name"]
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_INVALID, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_rollback_required_blocks(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["execution_plan"]["rollback_required"] = True
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_BLOCKED, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_missing_approval_blocks(self) -> None:
        self.assertEqual(STATUS_BLOCKED, self._gate(approval_context=None)["gate_status"])

    def test_approved_false_blocks(self) -> None:
        approval = dict(self.approval)
        approval["approved"] = False
        self.assertEqual(STATUS_BLOCKED, self._gate(approval_context=approval)["gate_status"])

    def test_approved_by_missing_blocks(self) -> None:
        approval = dict(self.approval)
        approval["approved_by"] = ""
        self.assertEqual(STATUS_BLOCKED, self._gate(approval_context=approval)["gate_status"])

    def test_approval_reason_missing_blocks(self) -> None:
        approval = dict(self.approval)
        approval["approval_reason"] = ""
        self.assertEqual(STATUS_BLOCKED, self._gate(approval_context=approval)["gate_status"])

    def test_approval_scope_error_invalid(self) -> None:
        approval = dict(self.approval)
        approval["approval_scope"] = "OTHER"
        self.assertEqual(STATUS_INVALID, self._gate(approval_context=approval)["gate_status"])

    def test_approval_plan_hash_mismatch_invalid(self) -> None:
        approval = dict(self.approval)
        approval["approved_plan_hash"] = "bad"
        self.assertEqual(STATUS_INVALID, self._gate(approval_context=approval)["gate_status"])

    def test_missing_token_blocks(self) -> None:
        self.assertEqual(STATUS_BLOCKED, self._gate(execution_token=None)["gate_status"])

    def test_token_scope_error_invalid(self) -> None:
        token = dict(self.token)
        token["scope"] = "OTHER"
        self.assertEqual(STATUS_INVALID, self._gate(execution_token=token)["gate_status"])

    def test_token_plan_hash_mismatch_invalid(self) -> None:
        token = dict(self.token)
        token["plan_hash"] = "bad"
        self.assertEqual(STATUS_INVALID, self._gate(execution_token=token)["gate_status"])

    def test_token_consumed_blocks(self) -> None:
        token = dict(self.token)
        token["consumed"] = True
        self.assertEqual(STATUS_BLOCKED, self._gate(execution_token=token)["gate_status"])

    def test_token_single_use_false_blocks(self) -> None:
        token = dict(self.token)
        token["single_use"] = False
        self.assertEqual(STATUS_BLOCKED, self._gate(execution_token=token)["gate_status"])

    def test_expected_plan_hash_mismatch_invalid(self) -> None:
        self.assertEqual(STATUS_INVALID, self._gate(expected_plan_hash="bad")["gate_status"])

    def test_rules_json_target_invalid(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["execution_steps"][0]["source"] = "routines/지표추종매매/rules.json"
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_INVALID, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_runtime_write_true_invalid(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["safety_flags"] = {"runtime_write": True}
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_INVALID, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_file_write_called_true_invalid(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["file_write_called"] = True
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_INVALID, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_actual_execution_true_invalid(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["actual_execution"] = True
        plan_hash = build_execution_plan_hash(plan)
        approval = dict(self.approval, approved_plan_hash=plan_hash)
        token = dict(self.token, plan_hash=plan_hash)
        self.assertEqual(STATUS_INVALID, self._gate(execution_plan=plan, approval_context=approval, execution_token=token, expected_plan_hash=plan_hash)["gate_status"])

    def test_all_output_safety_flags_false(self) -> None:
        result = self._gate()
        self.assertEqual(set(SAFETY_FLAG_NAMES), set(result["safety_flags"].keys()))
        for value in result["safety_flags"].values():
            self.assertFalse(value)

    def test_token_not_consumed(self) -> None:
        token = copy.deepcopy(self.token)
        self._gate(execution_token=token)
        self.assertFalse(token["consumed"])

    def test_approval_context_not_mutated(self) -> None:
        approval = copy.deepcopy(self.approval)
        original = copy.deepcopy(approval)
        self._gate(approval_context=approval)
        self.assertEqual(original, approval)

    def test_execution_token_not_mutated(self) -> None:
        token = copy.deepcopy(self.token)
        original = copy.deepcopy(token)
        self._gate(execution_token=token)
        self.assertEqual(original, token)

    def test_execution_plan_not_mutated(self) -> None:
        plan = copy.deepcopy(self.plan)
        original = copy.deepcopy(plan)
        self._gate(execution_plan=plan)
        self.assertEqual(original, plan)

    def test_no_file_created(self) -> None:
        before = set(PROJECT_ROOT.glob("*execution_gate*"))
        self._gate()
        self.assertEqual(before, set(PROJECT_ROOT.glob("*execution_gate*")))

    def test_other_m6_apis_not_called(self) -> None:
        with mock.patch("runtime_atomic_writer.write_json_atomic") as writer, mock.patch(
            "runtime_backup_manager.create_runtime_backup_plan"
        ) as backup, mock.patch(
            "runtime_rollback_manager.create_runtime_rollback_plan"
        ) as rollback, mock.patch(
            "runtime_commit_verifier.verify_runtime_commit"
        ) as verifier, mock.patch(
            "runtime_commit_audit_record.create_runtime_commit_audit_record"
        ) as audit, mock.patch(
            "runtime_commit_executor.create_runtime_commit_execution_plan"
        ) as executor:
            self._gate()
        writer.assert_not_called()
        backup.assert_not_called()
        rollback.assert_not_called()
        verifier.assert_not_called()
        audit.assert_not_called()
        executor.assert_not_called()

    def test_runtime_routines_unchanged(self) -> None:
        before = _protected_hashes()
        self._gate()
        self.assertEqual(before, _protected_hashes())

    def test_expected_steps_constant(self) -> None:
        self.assertEqual(
            (
                "VALIDATE_BOUNDARY",
                "PREPARE_BACKUP",
                "PREPARE_ATOMIC_WRITE",
                "VERIFY_COMMIT",
                "EVALUATE_ROLLBACK",
                "BUILD_AUDIT_RECORD",
                "COMPLETE",
            ),
            EXPECTED_STEP_SEQUENCE,
        )


if __name__ == "__main__":
    unittest.main()
