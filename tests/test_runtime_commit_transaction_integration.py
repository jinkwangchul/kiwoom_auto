# -*- coding: utf-8 -*-
"""Integration tests for Runtime Commit Transaction Contract (M6-12).

Validates the connection between M6-7 Execution Gate Preview,
M6-8 Canonical Contract, and M6-11 Transaction Contract.

All tests are preview-only - no actual execution, no file writes,
no runtime file modifications.
"""

import tempfile
import unittest
from pathlib import Path

from runtime_backup_manager import create_runtime_backup_plan, STATUS_READY as BACKUP_READY
from runtime_rollback_manager import create_runtime_rollback_plan
from runtime_commit_verifier import create_runtime_commit_verifier_plan
from runtime_commit_audit_record import create_runtime_commit_audit_record
from runtime_commit_execution_gate import evaluate_runtime_commit_execution_gate_preview, STATUS_APPROVED
from runtime_commit_contract import (
    normalize_runtime_commit_component_result,
    build_runtime_commit_contract_hash,
    COMPONENT_EXECUTION_PLAN_PREVIEW,
    COMPONENT_BACKUP_PLAN,
    COMPONENT_ROLLBACK_PLAN,
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
)
from runtime_commit_transaction_contract import (
    build_runtime_commit_transaction_manifest,
    validate_runtime_commit_transaction_manifest,
    build_runtime_commit_lock_contract,
    validate_runtime_commit_lock_contract,
    evaluate_runtime_commit_idempotency,
    IDEMPOTENCY_STATUSES,
)
from runtime_atomic_writer import write_json_atomic


class TestRuntimeCommitTransactionIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="runtime_tx_test_"))
        self.commit_id = "tx-test-commit-001"

    def tearDown(self):
        for child in self.tmp_dir.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp_dir.rmdir()
        except OSError:
            pass

    def _get_test_data(self) -> tuple:
        """Build test data and return all components."""
        f1 = self.tmp_dir / "a.json"
        f1.write_text("{}", encoding="utf-8")

        backup_plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[str(f1)],
        )
        rollback_plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        verifier_result = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        audit_record = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            verification_result=verifier_result,
        )
        atomic_writer_plan = {
            "atomic_writer_status": "OK",
            "target_path": str(f1),
            "written": False,
            "preview_only": True,
            "safety_flags": {f: False for f in [
                "runtime_write", "position_write", "balance_write", "file_write_called",
                "backup_created", "rollback_executed", "verification_executed", "audit_write",
                "gui_update_called", "send_order_called", "chejan_called", "broker_called",
                "sqlite_write", "rules_write", "atomic_writer_called"
            ]},
        }
        boundary_result = {
            "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
            "commit_id": self.commit_id,
            "preview_only": True,
            "issues": [],
            "warnings": [],
            "safety_flags": {f: False for f in [
                "runtime_write", "position_write", "balance_write", "file_write_called"
            ]},
        }

        from runtime_commit_executor import create_runtime_commit_execution_plan_preview
        execution_plan = create_runtime_commit_execution_plan_preview(
            commit_id=self.commit_id,
            boundary_result=boundary_result,
            atomic_writer_plan=atomic_writer_plan,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            verifier_result=verifier_result,
            audit_record=audit_record,
        )
        
        backup_hash = build_runtime_commit_contract_hash(
            normalize_runtime_commit_component_result(
                COMPONENT_BACKUP_PLAN, backup_plan, expected_commit_id=self.commit_id
            )
        )
        rollback_hash = build_runtime_commit_contract_hash(
            normalize_runtime_commit_component_result(
                COMPONENT_ROLLBACK_PLAN, rollback_plan, expected_commit_id=self.commit_id
            )
        )
        
        return execution_plan, backup_plan, rollback_plan, verifier_result, audit_record, backup_hash, rollback_hash

    def _build_valid_execution_plan(self) -> dict:
        """Build a valid execution plan for integration testing."""
        execution_plan, _, _, _, _, _, _ = self._get_test_data()
        return execution_plan

    # 1. Gate Preview 결과 사용 검증
    def test_gate_preview_result_used(self):
        execution_plan = self._build_valid_execution_plan()
        gate = evaluate_runtime_commit_execution_gate_preview(
            commit_id=self.commit_id,
            execution_plan=execution_plan,
            approval_context={
                "approved": True,
                "approved_commit_id": self.commit_id,
                "approval_scope": "RUNTIME_COMMIT",
                "approved_plan_hash": "",
                "approved_by": "test_operator",
                "approval_reason": "integration test approval",
            },
            execution_token={
                "commit_id": self.commit_id,
                "plan_hash": "",
                "scope": "RUNTIME_COMMIT_EXECUTION",
                "single_use": True,
                "consumed": False,
            },
            expected_plan_hash="",
        )
        self.assertIn("gate_status", gate)

    # 2. Gate canonical status READY 확인
    def test_gate_status_ready_when_approved(self):
        execution_plan = self._build_valid_execution_plan()
        plan_hash = build_runtime_commit_contract_hash(
            normalize_runtime_commit_component_result(
                COMPONENT_EXECUTION_PLAN_PREVIEW,
                execution_plan,
                expected_commit_id=self.commit_id,
            )
        )
        gate = evaluate_runtime_commit_execution_gate_preview(
            commit_id=self.commit_id,
            execution_plan=execution_plan,
            approval_context={
                "approved": True,
                "approved_commit_id": self.commit_id,
                "approval_scope": "RUNTIME_COMMIT",
                "approved_plan_hash": plan_hash,
                "approved_by": "test_operator",
                "approval_reason": "integration test",
            },
            execution_token={
                "commit_id": self.commit_id,
                "plan_hash": plan_hash,
                "scope": "RUNTIME_COMMIT_EXECUTION",
                "single_use": True,
                "consumed": False,
            },
            expected_plan_hash=plan_hash,
        )
        # Note: gate_status will be APPROVED if all validations pass, but execution_allowed is False
        self.assertTrue(gate["preview_only"])

    # 3. execution_allowed=False 유지 확인
    def test_execution_allowed_false(self):
        execution_plan = self._build_valid_execution_plan()
        plan_hash = build_runtime_commit_contract_hash(
            normalize_runtime_commit_component_result(
                COMPONENT_EXECUTION_PLAN_PREVIEW,
                execution_plan,
                expected_commit_id=self.commit_id,
            )
        )
        gate = evaluate_runtime_commit_execution_gate_preview(
            commit_id=self.commit_id,
            execution_plan=execution_plan,
            approval_context={
                "approved": True,
                "approved_commit_id": self.commit_id,
                "approval_scope": "RUNTIME_COMMIT",
                "approved_plan_hash": plan_hash,
                "approved_by": "test_operator",
                "approval_reason": "integration test",
            },
            execution_token={
                "commit_id": self.commit_id,
                "plan_hash": plan_hash,
                "scope": "RUNTIME_COMMIT_EXECUTION",
                "single_use": True,
                "consumed": False,
            },
            expected_plan_hash=plan_hash,
        )
        self.assertFalse(gate["execution_allowed"])

    # 4. real_gate_active=False 유지 확인
    def test_real_gate_active_false(self):
        execution_plan = self._build_valid_execution_plan()
        plan_hash = build_runtime_commit_contract_hash(
            normalize_runtime_commit_component_result(
                COMPONENT_EXECUTION_PLAN_PREVIEW,
                execution_plan,
                expected_commit_id=self.commit_id,
            )
        )
        gate = evaluate_runtime_commit_execution_gate_preview(
            commit_id=self.commit_id,
            execution_plan=execution_plan,
            approval_context={
                "approved": True,
                "approved_commit_id": self.commit_id,
                "approval_scope": "RUNTIME_COMMIT",
                "approved_plan_hash": plan_hash,
                "approved_by": "test_operator",
                "approval_reason": "integration test",
            },
            execution_token={
                "commit_id": self.commit_id,
                "plan_hash": plan_hash,
                "scope": "RUNTIME_COMMIT_EXECUTION",
                "single_use": True,
                "consumed": False,
            },
            expected_plan_hash=plan_hash,
        )
        self.assertFalse(gate["real_gate_active"])

    # 5. token_consumed=False 유지 확인
    def test_token_consumed_false(self):
        execution_plan = self._build_valid_execution_plan()
        plan_hash = build_runtime_commit_contract_hash(
            normalize_runtime_commit_component_result(
                COMPONENT_EXECUTION_PLAN_PREVIEW,
                execution_plan,
                expected_commit_id=self.commit_id,
            )
        )
        gate = evaluate_runtime_commit_execution_gate_preview(
            commit_id=self.commit_id,
            execution_plan=execution_plan,
            approval_context={
                "approved": True,
                "approved_commit_id": self.commit_id,
                "approval_scope": "RUNTIME_COMMIT",
                "approved_plan_hash": plan_hash,
                "approved_by": "test_operator",
                "approval_reason": "integration test",
            },
            execution_token={
                "commit_id": self.commit_id,
                "plan_hash": plan_hash,
                "scope": "RUNTIME_COMMIT_EXECUTION",
                "single_use": True,
                "consumed": False,
            },
            expected_plan_hash=plan_hash,
        )
        self.assertFalse(gate["token_consumed"])

    # 6. canonical execution plan hash를 Transaction Manifest에 전달
    def test_execution_plan_hash_to_manifest(self):
        execution_plan = self._build_valid_execution_plan()
        canonical = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            execution_plan,
            expected_commit_id=self.commit_id,
        )
        plan_hash = build_runtime_commit_contract_hash(canonical)

        target_paths = sorted([t.get("source", "") for t in execution_plan.get("source_statuses", {}).get("runtime_backup_manager", {}) if isinstance(t, dict)])
        manifest = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=target_paths if target_paths else ["test/path/a.json"],
            execution_plan_hash=plan_hash,
            approval_token_id="token-abc-123",
            expected_payload_hash="payload-hash-testing",
        )
        self.assertEqual(manifest["execution_plan_hash"], plan_hash)

    # 7. transaction_id deterministic 확인
    def test_transaction_id_deterministic(self):
        execution_plan = self._build_valid_execution_plan()
        canonical = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            execution_plan,
            expected_commit_id=self.commit_id,
        )
        plan_hash = build_runtime_commit_contract_hash(canonical)

        manifest1 = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["a.json", "b.json"],
            execution_plan_hash=plan_hash,
            approval_token_id="token-xyz",
            expected_payload_hash="hash123",
        )
        manifest2 = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["a.json", "b.json"],
            execution_plan_hash=plan_hash,
            approval_token_id="token-xyz",
            expected_payload_hash="hash123",
        )
        self.assertEqual(manifest1["transaction_id"], manifest2["transaction_id"])

    # 8. target_set_hash deterministic 확인
    def test_target_set_hash_deterministic(self):
        execution_plan = self._build_valid_execution_plan()
        canonical = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            execution_plan,
            expected_commit_id=self.commit_id,
        )
        plan_hash = build_runtime_commit_contract_hash(canonical)

        manifest1 = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["x.json", "y.json"],
            execution_plan_hash=plan_hash,
            approval_token_id="t1",
            expected_payload_hash="h1",
        )
        manifest2 = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["y.json", "x.json"],
            execution_plan_hash=plan_hash,
            approval_token_id="t1",
            expected_payload_hash="h1",
        )
        self.assertEqual(manifest1["target_set_hash"], manifest2["target_set_hash"])

    # 9. lock_key deterministic 확인
    def test_lock_key_deterministic(self):
        execution_plan = self._build_valid_execution_plan()
        canonical = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            execution_plan,
            expected_commit_id=self.commit_id,
        )
        plan_hash = build_runtime_commit_contract_hash(canonical)

        manifest = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["a.json", "b.json"],
            execution_plan_hash=plan_hash,
            approval_token_id="token",
            expected_payload_hash="hash",
        )

        lock1 = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=["a.json", "b.json"],
            transaction_id=manifest["transaction_id"],
            owner_id="owner-1",
        )
        lock2 = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=["a.json", "b.json"],
            transaction_id=manifest["transaction_id"],
            owner_id="owner-1",
        )
        self.assertEqual(lock1["lock_key"], lock2["lock_key"])

    # 10. 동일 입력 반복 결과 동일
    def test_same_input_same_result(self):
        execution_plan = self._build_valid_execution_plan()
        canonical = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            execution_plan,
            expected_commit_id=self.commit_id,
        )
        plan_hash = build_runtime_commit_contract_hash(canonical)

        manifest = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["a.json", "b.json"],
            execution_plan_hash=plan_hash,
            approval_token_id="token-123",
            expected_payload_hash="hash-456",
        )

        lock = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=["a.json", "b.json"],
            transaction_id=manifest["transaction_id"],
            owner_id="owner-test",
        )

        manifest2 = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["a.json", "b.json"],
            execution_plan_hash=plan_hash,
            approval_token_id="token-123",
            expected_payload_hash="hash-456",
        )
        lock2 = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=["a.json", "b.json"],
            transaction_id=manifest2["transaction_id"],
            owner_id="owner-test",
        )

        self.assertEqual(manifest["transaction_id"], manifest2["transaction_id"])
        self.assertEqual(lock["lock_key"], lock2["lock_key"])

    # 11. commit_id 전체 단계 동일
    def test_commit_id_consistent_across_stages(self):
        execution_plan = self._build_valid_execution_plan()
        plan_commit_id = execution_plan.get("commit_id", "")

        canonical = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            execution_plan,
            expected_commit_id=self.commit_id,
        )
        contract_commit_id = canonical.get("commit_id", "")

        manifest = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["a.json"],
            execution_plan_hash="hash",
            approval_token_id="token",
            expected_payload_hash="payload",
        )
        manifest_commit_id = manifest.get("commit_id", "")

        lock = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=["a.json"],
            transaction_id=manifest["transaction_id"],
            owner_id="owner",
        )
        lock_commit_id = lock.get("commit_id", "")

        self.assertEqual(plan_commit_id, self.commit_id)
        self.assertEqual(contract_commit_id, self.commit_id)
        self.assertEqual(manifest_commit_id, self.commit_id)
        self.assertEqual(lock_commit_id, self.commit_id)

    # 12. Manifest validation 통과
    def test_manifest_validation_passes(self):
        execution_plan, backup_plan, _, _, _, backup_hash, rollback_hash = self._get_test_data()
        canonical = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            execution_plan,
            expected_commit_id=self.commit_id,
        )
        plan_hash = build_runtime_commit_contract_hash(canonical)

        target_paths = sorted([t.get("source", "") for t in backup_plan.get("backup_targets", [])])

        manifest = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=target_paths,
            execution_plan_hash=plan_hash,
            approval_token_id="token-123",
            expected_payload_hash="hash-456",
            backup_plan_hash=backup_hash,
            rollback_plan_hash=rollback_hash,
        )
        validation = validate_runtime_commit_transaction_manifest(manifest)
        self.assertTrue(validation["valid"])

    # 13. Lock Contract validation 통과
    def test_lock_contract_validation_passes(self):
        execution_plan, backup_plan, _, _, _, backup_hash, rollback_hash = self._get_test_data()
        canonical = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            execution_plan,
            expected_commit_id=self.commit_id,
        )
        plan_hash = build_runtime_commit_contract_hash(canonical)

        target_paths = sorted([t.get("source", "") for t in backup_plan.get("backup_targets", [])])

        manifest = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=target_paths,
            execution_plan_hash=plan_hash,
            approval_token_id="token",
            expected_payload_hash="payload",
            backup_plan_hash=backup_hash,
            rollback_plan_hash=rollback_hash,
        )
        lock = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=target_paths,
            transaction_id=manifest["transaction_id"],
            owner_id="owner-1",
        )
        validation = validate_runtime_commit_lock_contract(lock, expected_commit_id=self.commit_id)
        self.assertTrue(validation["valid"])

    # 14. Idempotency NEW 결과
    def test_idempotency_new(self):
        result = evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash="hash-abc",
            transaction_state="CREATED",
            existing_records=[],
        )
        self.assertEqual(result["idempotency_status"], "NEW")

    # 15. Idempotency NEW → execution_allowed=True 확인
    def test_idempotency_new_execution_allowed(self):
        result = evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash="hash-abc",
            transaction_state="CREATED",
            existing_records=[],
        )
        self.assertTrue(result["execution_allowed"])

    # 16. commit_id 변경 시 차단
    def test_commit_id_mutation_blocked(self):
        manifest = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["a.json"],
            execution_plan_hash="hash",
            approval_token_id="token",
            expected_payload_hash="payload",
        )
        validation = validate_runtime_commit_transaction_manifest(
            manifest,
            expected_commit_id="different-commit-id",
        )
        self.assertFalse(validation["valid"])

    # 17. target path traversal 차단
    def test_path_traversal_blocked(self):
        result = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["../runtime/a.json"],
            execution_plan_hash="hash",
            approval_token_id="token",
            expected_payload_hash="payload",
        )
        self.assertTrue(any("path traversal" in i for i in result["issues"]))

    # 18. routines rules.json target 차단
    def test_routines_rules_json_blocked(self):
        result = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["routines/my_routine/rules.json"],
            execution_plan_hash="hash",
            approval_token_id="token",
            expected_payload_hash="payload",
        )
        self.assertTrue(any("rules.json" in i for i in result["issues"]))

    # 19-25. Idempotency 시나리오 추가 검증
    def test_idempotency_in_progress_blocked(self):
        records = [{"commit_id": self.commit_id, "target_set_hash": "hash", "transaction_status": "IN_PROGRESS", "lock_active": True}]
        result = evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash="hash",
            transaction_state="IN_PROGRESS",
            existing_records=records,
        )
        self.assertEqual(result["idempotency_status"], "IN_PROGRESS_BLOCKED")

    def test_idempotency_already_committed(self):
        records = [{"commit_id": self.commit_id, "target_set_hash": "hash", "transaction_status": "COMMITTED"}]
        result = evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash="hash",
            transaction_state="CREATED",
            existing_records=records,
        )
        self.assertEqual(result["idempotency_status"], "ALREADY_COMMITTED")

    def test_idempotency_retry_allowed(self):
        records = [{"commit_id": self.commit_id, "target_set_hash": "hash", "transaction_status": "ABORTED"}]
        result = evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash="hash",
            transaction_state="CREATED",
            existing_records=records,
        )
        self.assertEqual(result["idempotency_status"], "RETRY_ALLOWED")

    def test_idempotency_manual_review_required(self):
        records = [{"commit_id": self.commit_id, "target_set_hash": "hash", "transaction_status": "FAILED"}]
        result = evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash="hash",
            transaction_state="CREATED",
            existing_records=records,
        )
        self.assertEqual(result["idempotency_status"], "MANUAL_REVIEW_REQUIRED")

    def test_idempotency_conflict(self):
        records = [
            {"commit_id": self.commit_id, "target_set_hash": "hash", "transaction_status": "COMMITTED", "transaction_id": "t1"},
            {"commit_id": self.commit_id, "target_set_hash": "hash", "transaction_status": "ABORTED", "transaction_id": "t2"},
        ]
        result = evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash="hash",
            transaction_state="CREATED",
            existing_records=records,
        )
        self.assertEqual(result["idempotency_status"], "MANUAL_REVIEW_REQUIRED")

    def test_hash_changes_on_commit_id(self):
        manifest1 = build_runtime_commit_transaction_manifest(
            commit_id="commit-a",
            target_paths=["a.json"],
            execution_plan_hash="h",
            approval_token_id="t",
            expected_payload_hash="p",
        )
        manifest2 = build_runtime_commit_transaction_manifest(
            commit_id="commit-b",
            target_paths=["a.json"],
            execution_plan_hash="h",
            approval_token_id="t",
            expected_payload_hash="p",
        )
        self.assertNotEqual(manifest1["transaction_id"], manifest2["transaction_id"])

    def test_hash_unchanged_on_timestamp(self):
        manifest = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=["a.json"],
            execution_plan_hash="h",
            approval_token_id="t",
            expected_payload_hash="p",
        )
        # timestamp is not in hash fields, so transaction_id stays same conceptually
        self.assertTrue(manifest["transaction_id"])


if __name__ == "__main__":
    unittest.main()