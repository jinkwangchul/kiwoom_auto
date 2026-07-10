# -*- coding: utf-8 -*-
"""End-to-end integration tests for the Real Runtime Commit Executor (M7-01A).

These tests exercise ``execute_runtime_commit`` across the actual M6 runtime
commit chain. All writable paths are created under ``tempfile`` directories.
The project ``runtime`` and ``routines`` trees are only hashed for protection
checks and are never used as commit targets or storage roots.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime_backup_manager import create_runtime_backup_plan
from runtime_commit_approval_token_store import (
    create_runtime_commit_token_storage_plan,
    issue_runtime_commit_approval_token,
    read_runtime_commit_approval_token,
)
from runtime_commit_audit_record import create_runtime_commit_audit_record
from runtime_commit_execution_gate import (
    STATUS_APPROVED,
    STATUS_BLOCKED as GATE_BLOCKED,
    build_execution_plan_hash,
    evaluate_runtime_commit_execution_gate_preview,
)
from runtime_commit_executor import create_runtime_commit_execution_plan_preview
from runtime_commit_guard import (
    LOCK_STATUS_RELEASED,
    acquire_runtime_commit_lock,
    create_runtime_commit_guard_plan,
    read_runtime_commit_lock,
)
from runtime_commit_real_executor import (
    STATUS_BLOCKED,
    STATUS_COMMITTED,
    STATUS_ROLLED_BACK,
    execute_runtime_commit,
)
from runtime_commit_recovery_journal import RECOVERY_STATUS_COMPLETED
from runtime_commit_transaction_contract import build_runtime_commit_transaction_manifest
from runtime_commit_transaction_persistence import (
    create_runtime_transaction_storage_plan,
    read_runtime_transaction_journal,
)
from runtime_commit_verifier import create_runtime_commit_verifier_plan
from runtime_rollback_manager import create_runtime_rollback_plan


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_RUNTIME = PROJECT_ROOT / "runtime"
PROJECT_ROUTINES = PROJECT_ROOT / "routines"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(PROJECT_RUNTIME.glob("*.json")):
        if path.is_file():
            hashes[str(path)] = _sha256(path)
    for path in sorted(PROJECT_ROUTINES.glob("**/rules.json")):
        if path.is_file():
            hashes[str(path)] = _sha256(path)
    return hashes


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class RealExecutorE2EHarness:
    def __init__(self, testcase: unittest.TestCase, name: str) -> None:
        self.testcase = testcase
        self.tmp_dir = Path(tempfile.mkdtemp(prefix=f"m7_01a_{name}_"))
        self.storage_root = self.tmp_dir / "storage"
        self.target_path = self.tmp_dir / "targets" / f"{name}.json"
        self.target_path.parent.mkdir(parents=True, exist_ok=True)
        self.original_payload = {"value": "before", "case": name}
        self.new_payload = {"value": "after", "case": name}
        self.target_path.write_text(
            json.dumps(self.original_payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        digest = hashlib.sha256(str(self.tmp_dir).encode("utf-8")).hexdigest()[:12]
        self.commit_id = f"m7-01a-{name}-{digest}"
        self.token_id = f"token-{name}-{digest}"
        self.consumer_id = f"consumer-{name}-{digest}"
        self._build_components()

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _build_components(self) -> None:
        backup_plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[str(self.target_path)],
            backup_root=str(self.storage_root / "backup_preview"),
        )
        rollback_plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        verifier_plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        audit_record = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            verification_result=verifier_plan,
        )
        atomic_plan = {
            "atomic_writer_status": "OK",
            "target_path": str(self.target_path),
            "written": False,
            "preview_only": True,
            "safety_flags": {
                name: False
                for name in (
                    "runtime_write",
                    "position_write",
                    "balance_write",
                    "file_write_called",
                    "backup_created",
                    "rollback_executed",
                    "verification_executed",
                    "audit_write",
                    "gui_update_called",
                    "send_order_called",
                    "chejan_called",
                    "broker_called",
                    "sqlite_write",
                    "rules_write",
                    "atomic_writer_called",
                )
            },
        }
        boundary_result = {
            "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
            "commit_id": self.commit_id,
            "preview_only": True,
            "issues": [],
            "warnings": [],
            "safety_flags": {
                "runtime_write": False,
                "position_write": False,
                "balance_write": False,
                "file_write_called": False,
            },
        }
        self.execution_plan = create_runtime_commit_execution_plan_preview(
            commit_id=self.commit_id,
            boundary_result=boundary_result,
            atomic_writer_plan=atomic_plan,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            verifier_result=verifier_plan,
            audit_record=audit_record,
        )
        self.plan_hash = build_execution_plan_hash(self.execution_plan)
        self.transaction_manifest = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=[str(self.target_path)],
            execution_plan_hash=self.plan_hash,
            approval_token_id=self.token_id,
            expected_payload_hash="payload-hash-m7-01a",
            backup_plan_hash="backup-hash-m7-01a",
            rollback_plan_hash="rollback-hash-m7-01a",
        )
        self.storage_plan = create_runtime_transaction_storage_plan(
            storage_root=str(self.storage_root),
            commit_id=self.commit_id,
            transaction_id=self.transaction_manifest["transaction_id"],
        )
        self.guard_plan = create_runtime_commit_guard_plan(
            storage_root=str(self.storage_root),
            commit_id=self.commit_id,
            transaction_id=self.transaction_manifest["transaction_id"],
            target_set_hash=self.transaction_manifest["target_set_hash"],
            owner_id=self.consumer_id,
        )
        self.token_storage_plan = create_runtime_commit_token_storage_plan(
            storage_root=str(self.storage_root),
            token_id=self.token_id,
            commit_id=self.commit_id,
        )
        self.gate_result = self.build_gate()

    def build_gate(self, *, approved: bool = True) -> dict:
        return evaluate_runtime_commit_execution_gate_preview(
            commit_id=self.commit_id,
            execution_plan=self.execution_plan,
            approval_context={
                "approved": approved,
                "approved_commit_id": self.commit_id,
                "approval_scope": "RUNTIME_COMMIT",
                "approved_plan_hash": self.plan_hash,
                "approved_by": "m7-test-operator",
                "approval_reason": "m7-01a real executor integration",
            },
            execution_token={
                "commit_id": self.commit_id,
                "plan_hash": self.plan_hash,
                "scope": "RUNTIME_COMMIT_EXECUTION",
                "single_use": True,
                "consumed": False,
            },
            expected_plan_hash=self.plan_hash,
        )

    def issue_token(self, *, commit_id: str | None = None, plan_hash: str | None = None) -> dict:
        return issue_runtime_commit_approval_token(
            storage_plan=self.token_storage_plan,
            token={
                "token_id": self.token_id,
                "commit_id": commit_id or self.commit_id,
                "plan_hash": plan_hash or self.plan_hash,
                "issued_for": self.consumer_id,
                "issued_by": "m7-test-operator",
                "scope": "RUNTIME_COMMIT_EXECUTION",
                "single_use": True,
            },
        )

    def execute(
        self,
        *,
        gate_result: dict | None = None,
        guard_plan: dict | None = None,
        token_storage_plan: dict | None = None,
        expected_targets: dict | None = None,
        new_targets: dict | None = None,
        consumer_id: str | None = None,
    ) -> dict:
        return execute_runtime_commit(
            gate_result=gate_result or self.gate_result,
            transaction_manifest=self.transaction_manifest,
            storage_plan=self.storage_plan,
            guard_plan=guard_plan or self.guard_plan,
            token_storage_plan=token_storage_plan or self.token_storage_plan,
            expected_targets=expected_targets
            if expected_targets is not None
            else {str(self.target_path): copy.deepcopy(self.new_payload)},
            new_targets=new_targets
            if new_targets is not None
            else {str(self.target_path): copy.deepcopy(self.new_payload)},
            consumer_id=consumer_id or self.consumer_id,
        )

    def terminal_recovery_statuses(self) -> list[str]:
        statuses: list[str] = []
        for path in sorted(self.storage_root.glob("**/*.journal.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("is_terminal"):
                    statuses.append(event.get("status", ""))
        return statuses


class TestRuntimeCommitRealExecutorIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_before = _protected_hashes()

    def tearDown(self) -> None:
        self.assertEqual(self.protected_before, _protected_hashes())

    def _harness(self, name: str) -> RealExecutorE2EHarness:
        self.addCleanup(lambda h=None: None)
        harness = RealExecutorE2EHarness(self, name)
        self.addCleanup(harness.cleanup)
        return harness

    def test_01_normal_full_chain_commits_and_records_completed(self) -> None:
        h = self._harness("success")
        self.assertEqual(h.gate_result["gate_status"], STATUS_APPROVED)
        h.issue_token()

        gate_snapshot = copy.deepcopy(h.gate_result)
        manifest_snapshot = copy.deepcopy(h.transaction_manifest)
        storage_snapshot = copy.deepcopy(h.storage_plan)
        guard_snapshot = copy.deepcopy(h.guard_plan)
        token_plan_snapshot = copy.deepcopy(h.token_storage_plan)
        expected_targets = {str(h.target_path): copy.deepcopy(h.new_payload)}
        new_targets = {str(h.target_path): copy.deepcopy(h.new_payload)}
        expected_snapshot = copy.deepcopy(expected_targets)
        new_snapshot = copy.deepcopy(new_targets)

        result = h.execute(expected_targets=expected_targets, new_targets=new_targets)

        self.assertEqual(result["execute_status"], STATUS_COMMITTED)
        self.assertTrue(result["lock_acquired"])
        self.assertTrue(result["token_validated"])
        self.assertTrue(result["backup_created"])
        self.assertTrue(result["write_executed"])
        self.assertTrue(result["verify_passed"])
        self.assertTrue(result["token_consumed"])
        self.assertTrue(result["lock_released"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["manual_restore_required"])
        self.assertEqual(_read_json(h.target_path), h.new_payload)
        self.assertIn(RECOVERY_STATUS_COMPLETED, h.terminal_recovery_statuses())
        journal = read_runtime_transaction_journal(storage_plan=h.storage_plan)
        self.assertEqual(journal["read_status"], "OK")
        self.assertIn("COMPLETED", [event["stage"] for event in journal["events"]])
        self.assertEqual(h.gate_result, gate_snapshot)
        self.assertEqual(h.transaction_manifest, manifest_snapshot)
        self.assertEqual(h.storage_plan, storage_snapshot)
        self.assertEqual(h.guard_plan, guard_snapshot)
        self.assertEqual(h.token_storage_plan, token_plan_snapshot)
        self.assertEqual(expected_targets, expected_snapshot)
        self.assertEqual(new_targets, new_snapshot)

    def test_02_gate_blocked_prevents_guard_token_backup_and_write(self) -> None:
        h = self._harness("gate-blocked")
        h.issue_token()
        blocked_gate = h.build_gate(approved=False)
        self.assertEqual(blocked_gate["gate_status"], GATE_BLOCKED)
        before = h.target_path.read_text(encoding="utf-8")

        result = h.execute(gate_result=blocked_gate)

        self.assertEqual(result["execute_status"], STATUS_BLOCKED)
        self.assertFalse(result["lock_acquired"])
        self.assertFalse(result["token_validated"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["write_executed"])
        self.assertEqual(h.target_path.read_text(encoding="utf-8"), before)

    def test_03_guard_blocked_prevents_token_backup_and_write(self) -> None:
        h = self._harness("guard-blocked")
        h.issue_token()
        blocked_guard = copy.deepcopy(h.guard_plan)
        blocked_guard["guard_status"] = "BLOCKED"
        before = h.target_path.read_text(encoding="utf-8")

        result = h.execute(guard_plan=blocked_guard)

        self.assertEqual(result["execute_status"], STATUS_BLOCKED)
        self.assertTrue(result["gate_valid"])
        self.assertFalse(result["lock_acquired"])
        self.assertFalse(result["token_validated"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["write_executed"])
        token_read = read_runtime_commit_approval_token(storage_plan=h.token_storage_plan)
        self.assertEqual(token_read["token"]["token_status"], "ISSUED")
        self.assertEqual(h.target_path.read_text(encoding="utf-8"), before)

    def test_04_lock_conflict_blocks_second_execution_before_token_and_write(self) -> None:
        h = self._harness("lock-conflict")
        h.issue_token()
        acquire = acquire_runtime_commit_lock(guard_plan=h.guard_plan)
        self.assertEqual(acquire["acquire_status"], "ACQUIRED")
        before = h.target_path.read_text(encoding="utf-8")

        result = h.execute()

        self.assertEqual(result["execute_status"], STATUS_BLOCKED)
        self.assertFalse(result["token_validated"])
        self.assertFalse(result["token_consumed"])
        self.assertFalse(result["write_executed"])
        self.assertEqual(h.target_path.read_text(encoding="utf-8"), before)

    def test_05_missing_approval_token_blocks_and_releases_acquired_lock(self) -> None:
        h = self._harness("missing-token")
        before = h.target_path.read_text(encoding="utf-8")

        result = h.execute()

        self.assertEqual(result["execute_status"], STATUS_BLOCKED)
        self.assertTrue(result["lock_acquired"])
        self.assertTrue(result["lock_released"])
        self.assertFalse(result["token_validated"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["write_executed"])
        self.assertEqual(h.target_path.read_text(encoding="utf-8"), before)
        lock_read = read_runtime_commit_lock(guard_plan=h.guard_plan)
        self.assertEqual(lock_read["lock_record"]["lock_status"], LOCK_STATUS_RELEASED)

    def test_06_approval_token_mismatches_block_and_release_lock(self) -> None:
        cases = (
            ("commit-mismatch", {"commit_id": "different-commit-id"}),
            ("plan-hash-mismatch", {"plan_hash": "different-plan-hash"}),
        )
        for name, issue_kwargs in cases:
            with self.subTest(name=name):
                h = self._harness(name)
                h.issue_token(**issue_kwargs)
                before = h.target_path.read_text(encoding="utf-8")

                result = h.execute()

                self.assertEqual(result["execute_status"], STATUS_BLOCKED)
                self.assertTrue(result["lock_acquired"])
                self.assertTrue(result["lock_released"])
                self.assertFalse(result["token_consumed"])
                self.assertFalse(result["write_executed"])
                self.assertEqual(h.target_path.read_text(encoding="utf-8"), before)
                lock_read = read_runtime_commit_lock(guard_plan=h.guard_plan)
                self.assertEqual(lock_read["lock_record"]["lock_status"], LOCK_STATUS_RELEASED)

    def test_07_single_use_token_reuse_is_blocked_without_second_file_change(self) -> None:
        h = self._harness("token-reuse")
        h.issue_token()
        first = h.execute()
        self.assertEqual(first["execute_status"], STATUS_COMMITTED)
        after_first = h.target_path.read_text(encoding="utf-8")

        second = h.execute(new_targets={str(h.target_path): {"value": "second-write"}})

        self.assertEqual(second["execute_status"], STATUS_BLOCKED)
        self.assertFalse(second["token_consumed"])
        self.assertFalse(second["write_executed"])
        self.assertEqual(h.target_path.read_text(encoding="utf-8"), after_first)

    def test_08_verify_failure_rolls_back_restores_file_and_records_journal(self) -> None:
        h = self._harness("verify-rollback")
        h.issue_token()
        before_payload = _read_json(h.target_path)

        result = h.execute(
            expected_targets={str(h.target_path): {"expected": "different"}},
            new_targets={str(h.target_path): copy.deepcopy(h.new_payload)},
        )

        self.assertEqual(result["execute_status"], STATUS_ROLLED_BACK)
        self.assertTrue(result["rollback_executed"])
        self.assertFalse(result["verify_passed"])
        self.assertFalse(result["token_consumed"])
        self.assertTrue(result["lock_released"])
        self.assertFalse(result["manual_restore_required"])
        self.assertEqual(_read_json(h.target_path), before_payload)
        self.assertIn("ROLLED_BACK", h.terminal_recovery_statuses())
        journal = read_runtime_transaction_journal(storage_plan=h.storage_plan)
        self.assertEqual(journal["read_status"], "OK")
        self.assertEqual(journal["events"][-1]["stage"], "ROLLBACK_DONE")

    def test_09_persistence_manifest_failure_is_reported_before_write(self) -> None:
        h = self._harness("manifest-failure")
        h.issue_token()
        before = h.target_path.read_text(encoding="utf-8")

        with mock.patch(
            "runtime_commit_real_executor.write_runtime_transaction_manifest",
            side_effect=OSError("manifest write failed"),
        ):
            result = h.execute()

        self.assertNotEqual(result["execute_status"], STATUS_COMMITTED)
        self.assertTrue(result["lock_acquired"])
        self.assertTrue(result["lock_released"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["write_executed"])
        self.assertFalse(result["token_consumed"])
        self.assertIn("manifest", " ".join(result["issues"]).lower())
        self.assertEqual(h.target_path.read_text(encoding="utf-8"), before)

    def test_10_transaction_journal_failure_is_reported_before_write(self) -> None:
        h = self._harness("tx-journal-failure")
        h.issue_token()
        before = h.target_path.read_text(encoding="utf-8")

        with mock.patch(
            "runtime_commit_real_executor.append_runtime_transaction_journal_event",
            side_effect=OSError("journal append failed"),
        ):
            result = h.execute()

        self.assertNotEqual(result["execute_status"], STATUS_COMMITTED)
        self.assertTrue(result["lock_acquired"])
        self.assertTrue(result["lock_released"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["write_executed"])
        self.assertFalse(result["token_consumed"])
        self.assertIn("journal", " ".join(result["issues"]).lower())
        self.assertEqual(h.target_path.read_text(encoding="utf-8"), before)

    def test_11_recovery_journal_create_failure_is_reported(self) -> None:
        h = self._harness("recovery-create-failure")
        h.issue_token()

        with mock.patch(
            "runtime_commit_real_executor.create_recovery_journal",
            return_value={"create_status": "ERROR", "issues": ["recovery unavailable"]},
        ):
            result = h.execute()

        self.assertNotEqual(result["execute_status"], STATUS_COMMITTED)
        self.assertFalse(result["lock_acquired"])
        self.assertFalse(result["write_executed"])
        self.assertFalse(result["token_consumed"])
        self.assertIn("recovery", " ".join(result["issues"]).lower())

    def test_12_token_consume_failure_releases_lock_and_reports_release(self) -> None:
        h = self._harness("consume-failure")
        h.issue_token()

        with mock.patch(
            "runtime_commit_real_executor.consume_runtime_commit_approval_token",
            return_value={"consume_status": "BLOCKED", "issues": ["consume blocked"]},
        ):
            result = h.execute()

        self.assertEqual(result["execute_status"], STATUS_BLOCKED)
        self.assertTrue(result["lock_acquired"])
        self.assertTrue(result["lock_released"])
        self.assertTrue(result["write_executed"])
        self.assertTrue(result["verify_passed"])
        self.assertFalse(result["token_consumed"])
        lock_read = read_runtime_commit_lock(guard_plan=h.guard_plan)
        self.assertEqual(lock_read["lock_record"]["lock_status"], LOCK_STATUS_RELEASED)


if __name__ == "__main__":
    unittest.main()
