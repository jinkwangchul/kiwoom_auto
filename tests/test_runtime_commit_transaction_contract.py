# -*- coding: utf-8 -*-
"""Tests for Runtime Commit transaction/lock/idempotency contracts (M6-11)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import math
import unittest
from unittest import mock

from runtime_commit_transaction_contract import (
    LOCK_CONTRACT_VERSION,
    TRANSACTION_CONTRACT_VERSION,
    build_runtime_commit_lock_contract,
    build_runtime_commit_transaction_hash,
    build_runtime_commit_transaction_manifest,
    evaluate_runtime_commit_idempotency,
    validate_runtime_commit_lock_contract,
    validate_runtime_commit_transaction_manifest,
)


class RuntimeCommitTransactionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.commit_id = "commit-m6-11"
        self.target_paths = ["runtime/order_queue.json", "runtime/order_executions.json"]
        self.execution_plan_hash = "e" * 64
        self.approval_token_id = "token-1"
        self.expected_payload_hash = "p" * 64

    def manifest(self, **overrides):
        kwargs = {
            "commit_id": self.commit_id,
            "target_paths": list(self.target_paths),
            "execution_plan_hash": self.execution_plan_hash,
            "approval_token_id": self.approval_token_id,
            "expected_payload_hash": self.expected_payload_hash,
            "backup_plan_hash": "b" * 64,
            "rollback_plan_hash": "r" * 64,
            "metadata": {"source": "unit"},
        }
        kwargs.update(overrides)
        return build_runtime_commit_transaction_manifest(**kwargs)

    def test_manifest_created(self):
        manifest = self.manifest()
        self.assertEqual(TRANSACTION_CONTRACT_VERSION, manifest["contract_version"])
        self.assertEqual("CREATED", manifest["transaction_status"])
        self.assertEqual("MANIFEST_CREATED", manifest["current_stage"])
        self.assertFalse(manifest["recovery_required"])
        self.assertFalse(manifest["manual_restore_required"])

    def test_deterministic_transaction_id(self):
        self.assertEqual(self.manifest()["transaction_id"], self.manifest()["transaction_id"])

    def test_same_input_same_transaction_id(self):
        a = self.manifest()
        b = self.manifest()
        self.assertEqual(a["transaction_id"], b["transaction_id"])

    def test_target_change_changes_transaction_id(self):
        a = self.manifest()
        b = self.manifest(target_paths=["runtime/order_queue.json"])
        self.assertNotEqual(a["transaction_id"], b["transaction_id"])

    def test_execution_plan_hash_change_changes_transaction_id(self):
        a = self.manifest()
        b = self.manifest(execution_plan_hash="f" * 64)
        self.assertNotEqual(a["transaction_id"], b["transaction_id"])

    def test_empty_commit_id_invalid(self):
        manifest = self.manifest(commit_id="")
        self.assertFalse(validate_runtime_commit_transaction_manifest(manifest)["valid"])

    def test_whitespace_commit_id_invalid(self):
        manifest = self.manifest(commit_id=" bad ")
        self.assertFalse(validate_runtime_commit_transaction_manifest(manifest)["valid"])

    def test_empty_target_paths_invalid(self):
        manifest = self.manifest(target_paths=[])
        self.assertFalse(validate_runtime_commit_transaction_manifest(manifest)["valid"])

    def test_duplicate_target_invalid(self):
        manifest = self.manifest(target_paths=["runtime/a.json", "runtime/a.json"])
        self.assertFalse(validate_runtime_commit_transaction_manifest(manifest)["valid"])

    def test_windows_case_duplicate_target_invalid(self):
        manifest = self.manifest(target_paths=[r"runtime\A.json", "runtime/a.json"])
        self.assertFalse(validate_runtime_commit_transaction_manifest(manifest)["valid"])

    def test_path_traversal_invalid(self):
        manifest = self.manifest(target_paths=["runtime/../runtime/a.json"])
        self.assertFalse(validate_runtime_commit_transaction_manifest(manifest)["valid"])

    def test_routines_rules_json_invalid(self):
        manifest = self.manifest(target_paths=["routines/지표추종매매/rules.json"])
        self.assertFalse(validate_runtime_commit_transaction_manifest(manifest)["valid"])

    def test_path_object_invalid(self):
        manifest = self.manifest(target_paths=[Path("runtime/order_queue.json")])
        self.assertFalse(validate_runtime_commit_transaction_manifest(manifest)["valid"])

    def test_manifest_hash_deterministic(self):
        manifest = self.manifest()
        self.assertEqual(build_runtime_commit_transaction_hash(manifest), build_runtime_commit_transaction_hash(manifest))

    def test_metadata_change_keeps_hash(self):
        a = self.manifest(metadata={"timestamp": "1"})
        b = self.manifest(metadata={"timestamp": "2"})
        self.assertEqual(build_runtime_commit_transaction_hash(a), build_runtime_commit_transaction_hash(b))

    def test_warnings_change_keeps_hash(self):
        a = self.manifest()
        b = self.manifest()
        b["warnings"] = ["new"]
        self.assertEqual(build_runtime_commit_transaction_hash(a), build_runtime_commit_transaction_hash(b))

    def test_payload_change_changes_hash(self):
        a = self.manifest()
        b = self.manifest(expected_payload_hash="q" * 64)
        self.assertNotEqual(build_runtime_commit_transaction_hash(a), build_runtime_commit_transaction_hash(b))

    def test_nan_in_metadata_invalid(self):
        manifest = self.manifest(metadata={"bad": math.nan})
        self.assertFalse(validate_runtime_commit_transaction_manifest(manifest)["valid"])

    def test_infinity_in_metadata_invalid(self):
        manifest = self.manifest(metadata={"bad": math.inf})
        self.assertFalse(validate_runtime_commit_transaction_manifest(manifest)["valid"])

    def test_lock_contract_created(self):
        manifest = self.manifest()
        lock = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=self.target_paths,
            transaction_id=manifest["transaction_id"],
            owner_id="operator",
        )
        self.assertEqual(LOCK_CONTRACT_VERSION, lock["contract_version"])
        self.assertEqual("LOCK_REQUESTED", lock["lock_status"])
        self.assertTrue(validate_runtime_commit_lock_contract(lock)["valid"])

    def test_deterministic_lock_key(self):
        manifest = self.manifest()
        a = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=self.target_paths,
            transaction_id=manifest["transaction_id"],
            owner_id="operator",
        )
        b = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=self.target_paths,
            transaction_id=manifest["transaction_id"],
            owner_id="operator",
        )
        self.assertEqual(a["lock_key"], b["lock_key"])

    def test_target_set_change_changes_lock_key(self):
        manifest = self.manifest()
        a = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=["runtime/a.json"],
            transaction_id=manifest["transaction_id"],
            owner_id="operator",
        )
        b = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=["runtime/b.json"],
            transaction_id=manifest["transaction_id"],
            owner_id="operator",
        )
        self.assertNotEqual(a["lock_key"], b["lock_key"])

    def test_owner_id_missing_invalid(self):
        manifest = self.manifest()
        lock = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=self.target_paths,
            transaction_id=manifest["transaction_id"],
            owner_id="",
        )
        self.assertFalse(validate_runtime_commit_lock_contract(lock)["valid"])

    def test_reentrant_false(self):
        manifest = self.manifest()
        lock = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=self.target_paths,
            transaction_id=manifest["transaction_id"],
            owner_id="operator",
        )
        self.assertFalse(lock["reentrant"])

    def test_no_actual_lock_acquired(self):
        manifest = self.manifest()
        lock = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=self.target_paths,
            transaction_id=manifest["transaction_id"],
            owner_id="operator",
        )
        self.assertFalse(lock["safety_flags"]["lock_acquired"])

    def test_idempotency_new(self):
        result = evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash=self.manifest()["target_set_hash"],
            transaction_state="CREATED",
            existing_records=[],
        )
        self.assertEqual("NEW", result["idempotency_status"])
        self.assertTrue(result["execution_allowed"])

    def test_idempotency_in_progress_blocked(self):
        result = self.idempotency_with([{"transaction_status": "IN_PROGRESS", "current_stage": "LOCK_ACQUIRED"}])
        self.assertEqual("IN_PROGRESS_BLOCKED", result["idempotency_status"])
        self.assertFalse(result["execution_allowed"])

    def test_idempotency_already_committed(self):
        result = self.idempotency_with([{"transaction_status": "COMMITTED", "current_stage": "COMPLETED"}])
        self.assertEqual("ALREADY_COMMITTED", result["idempotency_status"])
        self.assertFalse(result["execution_allowed"])

    def test_idempotency_retry_allowed(self):
        result = self.idempotency_with([{"transaction_status": "ABORTED", "current_stage": "LOCK_PENDING"}])
        self.assertEqual("RETRY_ALLOWED", result["idempotency_status"])
        self.assertTrue(result["execution_allowed"])

    def test_idempotency_recovery_required(self):
        result = self.idempotency_with([{"transaction_status": "IN_PROGRESS", "current_stage": "WRITE_STARTED"}])
        self.assertEqual("RECOVERY_REQUIRED", result["idempotency_status"])
        self.assertTrue(result["recovery_required"])

    def test_idempotency_manual_review_required(self):
        result = self.idempotency_with([{"transaction_status": "FAILED", "current_stage": "FAILED"}])
        self.assertEqual("MANUAL_REVIEW_REQUIRED", result["idempotency_status"])
        self.assertTrue(result["manual_review_required"])

    def test_idempotency_committed_in_progress_conflict(self):
        result = self.idempotency_with([
            {"transaction_status": "COMMITTED", "current_stage": "COMPLETED"},
            {"transaction_status": "IN_PROGRESS", "current_stage": "LOCK_ACQUIRED"},
        ])
        self.assertEqual("MANUAL_REVIEW_REQUIRED", result["idempotency_status"])

    def test_idempotency_different_transaction_id_final_conflict(self):
        result = self.idempotency_with([
            {"transaction_id": "a", "transaction_status": "FAILED", "current_stage": "FAILED"},
            {"transaction_id": "b", "transaction_status": "MANUAL_RESTORE_REQUIRED", "current_stage": "FAILED"},
        ])
        self.assertEqual("MANUAL_REVIEW_REQUIRED", result["idempotency_status"])

    def test_unknown_transaction_state_invalid(self):
        result = evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash=self.manifest()["target_set_hash"],
            transaction_state="UNKNOWN",
            existing_records=[],
        )
        self.assertEqual("INVALID", result["idempotency_status"])

    def test_execution_allowed_values(self):
        self.assertTrue(self.idempotency_with([])["execution_allowed"])
        self.assertFalse(self.idempotency_with([{"transaction_status": "COMMITTED"}])["execution_allowed"])

    def test_source_mutation_none(self):
        records = [self.record({"transaction_status": "ABORTED", "current_stage": "LOCK_PENDING"})]
        snapshot = deepcopy(records)
        evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash=self.manifest()["target_set_hash"],
            transaction_state="CREATED",
            existing_records=records,
        )
        self.assertEqual(records, snapshot)

    def test_all_safety_flags_false(self):
        manifest = self.manifest()
        lock = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=self.target_paths,
            transaction_id=manifest["transaction_id"],
            owner_id="operator",
        )
        idem = evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash=manifest["target_set_hash"],
            transaction_state="CREATED",
            existing_records=[],
        )
        for source in (manifest, lock, idem):
            self.assertTrue(all(value is False for value in source["safety_flags"].values()))

    def test_no_file_creation(self):
        with mock.patch("runtime_atomic_writer.write_json_atomic") as writer:
            self.manifest()
        writer.assert_not_called()

    def test_existing_m6_apis_not_called(self):
        with mock.patch("runtime_backup_manager.create_runtime_backup_plan") as backup:
            self.manifest()
        backup.assert_not_called()

    def test_runtime_routines_not_touched_by_contracts(self):
        manifest = self.manifest()
        validate_runtime_commit_transaction_manifest(manifest)
        lock = build_runtime_commit_lock_contract(
            commit_id=self.commit_id,
            target_paths=self.target_paths,
            transaction_id=manifest["transaction_id"],
            owner_id="operator",
        )
        validate_runtime_commit_lock_contract(lock)

    def record(self, overrides):
        base = {
            "commit_id": self.commit_id,
            "target_set_hash": self.manifest()["target_set_hash"],
            "transaction_id": self.manifest()["transaction_id"],
            "transaction_status": "CREATED",
            "current_stage": "MANIFEST_CREATED",
        }
        base.update(overrides)
        return base

    def idempotency_with(self, records):
        return evaluate_runtime_commit_idempotency(
            commit_id=self.commit_id,
            target_set_hash=self.manifest()["target_set_hash"],
            transaction_state="CREATED",
            existing_records=[self.record(record) for record in records],
        )


if __name__ == "__main__":
    unittest.main()
