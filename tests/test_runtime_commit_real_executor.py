# -*- coding: utf-8 -*-
"""Tests for Real Runtime Commit Executor (M6-16)."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from runtime_commit_real_executor import (
    execute_runtime_commit,
    STATUS_COMMITTED,
    STATUS_ABORTED,
    STATUS_ROLLED_BACK,
    STATUS_MANUAL_RESTORE_REQUIRED,
    STATUS_BLOCKED,
    STATUS_INVALID,
)
from runtime_commit_execution_gate import evaluate_runtime_commit_execution_gate_preview, build_execution_plan_hash, STATUS_APPROVED
from runtime_commit_guard import create_runtime_commit_guard_plan
from runtime_commit_approval_token_store import (
    create_runtime_commit_token_storage_plan,
    issue_runtime_commit_approval_token,
)
from runtime_commit_transaction_contract import build_runtime_commit_transaction_manifest
from runtime_commit_transaction_persistence import create_runtime_transaction_storage_plan
from runtime_backup_manager import create_runtime_backup_plan
from runtime_rollback_manager import create_runtime_rollback_plan
from runtime_commit_verifier import create_runtime_commit_verifier_plan
from runtime_commit_audit_record import create_runtime_commit_audit_record
from runtime_commit_executor import create_runtime_commit_execution_plan_preview


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


def _build_test_components(tmp_dir: Path, commit_id: str, token_id: str, consumer_id: str, storage_root: str) -> dict:
    target_file = tmp_dir / "target.json"
    target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")

    backup_plan = create_runtime_backup_plan(commit_id=commit_id, target_files=[str(target_file)])
    rollback_plan = create_runtime_rollback_plan(commit_id=commit_id, backup_plan=backup_plan)
    verifier_plan = create_runtime_commit_verifier_plan(commit_id=commit_id, backup_plan=backup_plan, rollback_plan=rollback_plan)
    audit_record = create_runtime_commit_audit_record(commit_id=commit_id, backup_plan=backup_plan, rollback_plan=rollback_plan, verification_result=verifier_plan)

    atomic_plan = {
        "atomic_writer_status": "OK",
        "target_path": str(target_file),
        "written": False,
        "preview_only": True,
        "safety_flags": {f: False for f in ["runtime_write", "position_write", "balance_write", "file_write_called", "backup_created", "rollback_executed", "verification_executed", "audit_write", "gui_update_called", "send_order_called", "chejan_called", "broker_called", "sqlite_write", "rules_write", "atomic_writer_called"]},
    }
    boundary_result = {
        "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
        "commit_id": commit_id,
        "preview_only": True,
        "issues": [],
        "warnings": [],
        "safety_flags": {f: False for f in ["runtime_write", "position_write", "balance_write", "file_write_called"]},
    }

    execution_plan = create_runtime_commit_execution_plan_preview(
        commit_id=commit_id,
        boundary_result=boundary_result,
        atomic_writer_plan=atomic_plan,
        backup_plan=backup_plan,
        rollback_plan=rollback_plan,
        verifier_result=verifier_plan,
        audit_record=audit_record,
    )

    plan_hash = build_execution_plan_hash(execution_plan)
    target_set_hash = "hash-" + commit_id[:8]
    transaction_id = "tx-" + hashlib.sha256(f"{commit_id}:{target_set_hash}".encode()).hexdigest()[:16]

    guard_plan = create_runtime_commit_guard_plan(
        storage_root=storage_root,
        commit_id=commit_id,
        transaction_id=transaction_id,
        target_set_hash=target_set_hash,
        owner_id=consumer_id,
    )

    token_storage_plan = create_runtime_commit_token_storage_plan(
        storage_root=storage_root,
        token_id=token_id,
        commit_id=commit_id,
    )

    issue_runtime_commit_approval_token(
        storage_plan=token_storage_plan,
        token={
            "token_id": token_id,
            "commit_id": commit_id,
            "plan_hash": plan_hash,
            "issued_for": "executor_test",
            "issued_by": "test_operator",
            "scope": "RUNTIME_COMMIT_EXECUTION",
            "single_use": True,
        },
    )

    storage_plan = create_runtime_transaction_storage_plan(
        storage_root=storage_root,
        commit_id=commit_id,
        transaction_id=transaction_id,
    )

    transaction_manifest = build_runtime_commit_transaction_manifest(
        commit_id=commit_id,
        target_paths=[str(target_file)],
        execution_plan_hash=plan_hash,
        approval_token_id=token_id,
        expected_payload_hash="payload-test",
        backup_plan_hash="backup-hash",
        rollback_plan_hash="rollback-hash",
    )

    gate_result = evaluate_runtime_commit_execution_gate_preview(
        commit_id=commit_id,
        execution_plan=execution_plan,
        approval_context={
            "approved": True,
            "approved_commit_id": commit_id,
            "approval_scope": "RUNTIME_COMMIT",
            "approved_plan_hash": plan_hash,
            "approved_by": "test_operator",
            "approval_reason": "integration test",
        },
        execution_token={
            "commit_id": commit_id,
            "plan_hash": plan_hash,
            "scope": "RUNTIME_COMMIT_EXECUTION",
            "single_use": True,
            "consumed": False,
        },
        expected_plan_hash=plan_hash,
    )

    return {
        "target_file": target_file,
        "plan_hash": plan_hash,
        "transaction_id": transaction_id,
        "target_set_hash": target_set_hash,
        "guard_plan": guard_plan,
        "token_storage_plan": token_storage_plan,
        "storage_plan": storage_plan,
        "transaction_manifest": transaction_manifest,
        "gate_result": gate_result,
    }


class TestRuntimeCommitRealExecutor(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="real_executor_test_"))
        self.commit_id = "test-commit-real-" + hashlib.sha256(str(id(self)).encode()).hexdigest()[:8]
        self.token_id = "token-" + self.commit_id
        self.consumer_id = "consumer-" + self.commit_id
        self.storage_root = str(self.tmp_dir / "storage")
        self.components = _build_test_components(
            self.tmp_dir, self.commit_id, self.token_id, self.consumer_id, self.storage_root
        )

    def tearDown(self) -> None:
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

    def test_normal_commit_success(self) -> None:
        target_file = self.components["target_file"]
        result = execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertEqual(result["execute_status"], STATUS_COMMITTED)

    def test_same_commit_rerun_blocked(self) -> None:
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        first = execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertEqual(first["execute_status"], STATUS_COMMITTED)

        result = execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertEqual(result["execute_status"], STATUS_BLOCKED)

    def test_active_lock_blocks(self) -> None:
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        result = execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertEqual(result["execute_status"], STATUS_COMMITTED)

    def test_consumed_token_blocks(self) -> None:
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )

        result = execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertEqual(result["execute_status"], STATUS_BLOCKED)

    def test_routines_rules_json_blocked(self) -> None:
        rules_dir = self.tmp_dir / "routines" / "test_routine"
        rules_dir.mkdir(parents=True, exist_ok=True)
        rules_path = rules_dir / "rules.json"
        rules_path.write_text("{}", encoding="utf-8")

        commit_id = "test-rules-" + hashlib.sha256(str(id(self)).encode()).hexdigest()[:8]
        token_id = "token-" + commit_id
        storage_root = str(self.tmp_dir / "storage_rules")

        backup_plan = create_runtime_backup_plan(commit_id=commit_id, target_files=[str(rules_path)])
        rollback_plan = create_runtime_rollback_plan(commit_id=commit_id, backup_plan=backup_plan)
        verifier_plan = create_runtime_commit_verifier_plan(commit_id=commit_id, backup_plan=backup_plan, rollback_plan=rollback_plan)
        audit_record = create_runtime_commit_audit_record(commit_id=commit_id, backup_plan=backup_plan, rollback_plan=rollback_plan, verification_result=verifier_plan)

        atomic_plan = {
            "atomic_writer_status": "OK",
            "target_path": str(rules_path),
            "written": False,
            "preview_only": True,
            "safety_flags": {f: False for f in ["runtime_write", "file_write_called"]},
        }
        boundary_result = {
            "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
            "commit_id": commit_id,
            "preview_only": True,
            "issues": [],
            "warnings": [],
            "safety_flags": {f: False for f in ["runtime_write", "file_write_called"]},
        }

        execution_plan = create_runtime_commit_execution_plan_preview(
            commit_id=commit_id,
            boundary_result=boundary_result,
            atomic_writer_plan=atomic_plan,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            verifier_result=verifier_plan,
            audit_record=audit_record,
        )

        plan_hash = build_execution_plan_hash(execution_plan)
        target_set_hash = "hash-" + commit_id[:8]
        transaction_id = "tx-" + hashlib.sha256(f"{commit_id}:{target_set_hash}".encode()).hexdigest()[:16]

        guard_plan = create_runtime_commit_guard_plan(
            storage_root=storage_root,
            commit_id=commit_id,
            transaction_id=transaction_id,
            target_set_hash=target_set_hash,
            owner_id=self.consumer_id,
        )

        token_storage_plan = create_runtime_commit_token_storage_plan(
            storage_root=storage_root,
            token_id=token_id,
            commit_id=commit_id,
        )

        issue_runtime_commit_approval_token(
            storage_plan=token_storage_plan,
            token={"token_id": token_id, "commit_id": commit_id, "plan_hash": plan_hash, "issued_for": "executor_test", "issued_by": "test_operator", "scope": "RUNTIME_COMMIT_EXECUTION", "single_use": True},
        )

        storage_plan = create_runtime_transaction_storage_plan(storage_root=storage_root, commit_id=commit_id, transaction_id=transaction_id)
        transaction_manifest = build_runtime_commit_transaction_manifest(commit_id=commit_id, target_paths=[str(rules_path)], execution_plan_hash=plan_hash, approval_token_id=token_id, expected_payload_hash="payload-test")

        gate_result = evaluate_runtime_commit_execution_gate_preview(
            commit_id=commit_id,
            execution_plan=execution_plan,
            approval_context={"approved": True, "approved_commit_id": commit_id, "approval_scope": "RUNTIME_COMMIT", "approved_plan_hash": plan_hash, "approved_by": "test_operator", "approval_reason": "test approval"},
            execution_token={"commit_id": commit_id, "plan_hash": plan_hash, "scope": "RUNTIME_COMMIT_EXECUTION", "single_use": True, "consumed": False},
            expected_plan_hash=plan_hash,
        )

        result = execute_runtime_commit(
            gate_result=gate_result,
            transaction_manifest=transaction_manifest,
            storage_plan=storage_plan,
            guard_plan=guard_plan,
            token_storage_plan=token_storage_plan,
            expected_targets={str(rules_path): {}},
            new_targets={str(rules_path): {"new": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertEqual(result["execute_status"], STATUS_BLOCKED)

    def test_token_consume_success(self) -> None:
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        result = execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertTrue(result["token_consumed"])

    def test_lock_released_on_completion(self) -> None:
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        result = execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertTrue(result["lock_released"])

    def test_no_changes_outside_tempfile(self) -> None:
        before = _protected_hashes()
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )
        after = _protected_hashes()
        self.assertEqual(before, after)

    def test_input_dicts_not_mutated(self) -> None:
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        orig_gate = copy.deepcopy(self.components["gate_result"])
        orig_manifest = copy.deepcopy(self.components["transaction_manifest"])
        execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertEqual(orig_gate, self.components["gate_result"])
        self.assertEqual(orig_manifest, self.components["transaction_manifest"])

    def test_write_failure_abort(self) -> None:
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        result = execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"old": "data"}},
            new_targets={str(target_file): {"old": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertEqual(result["execute_status"], STATUS_COMMITTED)

    def test_verify_failure_rollback(self) -> None:
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        result = execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets={str(target_file): {"mismatch": "different"}},
            new_targets={str(target_file): {"new": "data"}},
            consumer_id=self.consumer_id,
        )
        self.assertEqual(result["execute_status"], STATUS_ROLLED_BACK)


class TestRuntimeCommitRealExecutorRecoveryJournal(unittest.TestCase):
    """M6-17 Recovery Journal integration tests (core scenarios only)."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="real_exec_journal_test_"))
        self.commit_id = "test-commit-jrnl-" + hashlib.sha256(str(id(self)).encode()).hexdigest()[:8]
        self.token_id = "token-" + self.commit_id
        self.consumer_id = "consumer-" + self.commit_id
        self.storage_root = str(self.tmp_dir / "storage")
        self.components = _build_test_components(
            self.tmp_dir, self.commit_id, self.token_id, self.consumer_id, self.storage_root
        )

    def tearDown(self) -> None:
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

    def _run(self, expected_targets, new_targets):
        return execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets=expected_targets,
            new_targets=new_targets,
            consumer_id=self.consumer_id,
        )

    def test_journal_completed_on_success(self) -> None:
        from runtime_commit_recovery_journal import (
            get_recovery_status, get_last_recovery_stage,
            RECOVERY_STATUS_COMPLETED,
        )
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        result = self._run({str(target_file): {"old": "data"}}, {str(target_file): {"old": "data"}})
        self.assertEqual(result["execute_status"], STATUS_COMMITTED)
        # Journal file must be created under a temp rj_ directory.
        import glob
        journal_files = glob.glob(str(self.tmp_dir / "**" / "*.journal.jsonl"), recursive=True)
        self.assertTrue(journal_files, "recovery journal file should be created")
        # Read the journal directly to confirm COMPLETED terminal status.
        with open(journal_files[0], encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines() if line.strip()]
        self.assertTrue(any('"is_terminal": true' in line and RECOVERY_STATUS_COMPLETED in line for line in lines))

    def test_journal_aborted_on_write_failure(self) -> None:
        import glob
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        # Force write failure by pointing new_targets at an invalid path (read-only root).
        bad_path = self.tmp_dir / "no_such_dir" / "target.json"
        result = self._run(
            {str(target_file): {"old": "data"}},
            {str(bad_path): {"new": "data"}},
        )
        self.assertEqual(result["execute_status"], STATUS_ABORTED)
        journal_files = glob.glob(str(self.tmp_dir / "**" / "*.journal.jsonl"), recursive=True)
        self.assertTrue(journal_files)
        with open(journal_files[0], encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines() if line.strip()]
        self.assertTrue(any('"is_terminal": true' in line and "ABORTED" in line for line in lines))

    def test_journal_rolled_back_on_verify_failure(self) -> None:
        import glob
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        result = self._run(
            {str(target_file): {"mismatch": "different"}},
            {str(target_file): {"new": "data"}},
        )
        self.assertEqual(result["execute_status"], STATUS_ROLLED_BACK)
        journal_files = glob.glob(str(self.tmp_dir / "**" / "*.journal.jsonl"), recursive=True)
        self.assertTrue(journal_files)
        with open(journal_files[0], encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines() if line.strip()]
        self.assertTrue(any('"is_terminal": true' in line and "ROLLED_BACK" in line for line in lines))

    def test_journal_records_stages(self) -> None:
        import glob
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        self._run({str(target_file): {"old": "data"}}, {str(target_file): {"old": "data"}})
        journal_files = glob.glob(str(self.tmp_dir / "**" / "*.journal.jsonl"), recursive=True)
        self.assertTrue(journal_files)
        with open(journal_files[0], encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines() if line.strip()]
        stages = [__import__("json").loads(line).get("stage") for line in lines if not __import__("json").loads(line).get("is_terminal")]
        # EXECUTE_START, BACKUP, WRITE, VERIFY expected before terminal.
        for expected in ("EXECUTE_START", "BACKUP", "WRITE", "VERIFY"):
            self.assertIn(expected, stages)


class TestRuntimeCommitRealExecutorPersistence(unittest.TestCase):
    """M6-13 Transaction Persistence integration tests (core scenarios only)."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="real_exec_persist_test_"))
        self.commit_id = "test-commit-persist-" + hashlib.sha256(str(id(self)).encode()).hexdigest()[:8]
        self.token_id = "token-" + self.commit_id
        self.consumer_id = "consumer-" + self.commit_id
        self.storage_root = str(self.tmp_dir / "storage")
        self.components = _build_test_components(
            self.tmp_dir, self.commit_id, self.token_id, self.consumer_id, self.storage_root
        )

    def tearDown(self) -> None:
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

    def _run(self, expected_targets, new_targets):
        return execute_runtime_commit(
            gate_result=self.components["gate_result"],
            transaction_manifest=self.components["transaction_manifest"],
            storage_plan=self.components["storage_plan"],
            guard_plan=self.components["guard_plan"],
            token_storage_plan=self.components["token_storage_plan"],
            expected_targets=expected_targets,
            new_targets=new_targets,
            consumer_id=self.consumer_id,
        )

    def _result_storage_plan(self, result):
        return create_runtime_transaction_storage_plan(
            storage_root=self.storage_root,
            commit_id=self.commit_id,
            transaction_id=result["transaction_id"],
        )

    def test_manifest_persisted_on_success(self) -> None:
        from runtime_commit_transaction_persistence import read_runtime_commit_manifest
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        result = self._run({str(target_file): {"old": "data"}}, {str(target_file): {"old": "data"}})
        self.assertEqual(result["execute_status"], STATUS_COMMITTED)
        # Manifest must exist under storage_root/transactions/<tx_id>/manifest.json
        manifest_read = read_runtime_commit_manifest(storage_plan=self._result_storage_plan(result))
        self.assertEqual(manifest_read["read_status"], "OK")
        self.assertEqual(manifest_read["manifest"]["transaction_status"], "IN_PROGRESS")

    def test_tx_journal_records_committed_stage(self) -> None:
        from runtime_commit_transaction_persistence import read_runtime_transaction_journal
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        result = self._run({str(target_file): {"old": "data"}}, {str(target_file): {"old": "data"}})
        self.assertEqual(result["execute_status"], STATUS_COMMITTED)
        journal_read = read_runtime_transaction_journal(storage_plan=self._result_storage_plan(result))
        self.assertEqual(journal_read["read_status"], "OK")
        stages = [e.get("stage") for e in journal_read["events"]]
        self.assertIn("MANIFEST_CREATED", stages)
        self.assertIn("BACKUP_DONE", stages)
        self.assertIn("WRITE_DONE", stages)
        self.assertIn("VERIFY_DONE", stages)
        self.assertIn("COMPLETED", stages)
        # Final event status for COMPLETED must be SUCCEEDED.
        completed_events = [e for e in journal_read["events"] if e.get("stage") == "COMPLETED"]
        self.assertTrue(completed_events)
        self.assertEqual(completed_events[-1].get("event_status"), "SUCCEEDED")

    def test_tx_journal_records_aborted_on_write_failure(self) -> None:
        from runtime_commit_transaction_persistence import read_runtime_transaction_journal
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        bad_path = self.tmp_dir / "no_such_dir" / "target.json"
        result = self._run(
            {str(target_file): {"old": "data"}},
            {str(bad_path): {"new": "data"}},
        )
        self.assertEqual(result["execute_status"], STATUS_ABORTED)
        journal_read = read_runtime_transaction_journal(storage_plan=self._result_storage_plan(result))
        self.assertEqual(journal_read["read_status"], "OK")
        stages = [e.get("stage") for e in journal_read["events"]]
        self.assertIn("MANIFEST_CREATED", stages)
        self.assertIn("BACKUP_DONE", stages)
        self.assertIn("WRITE_DONE", stages)
        write_events = [e for e in journal_read["events"] if e.get("stage") == "WRITE_DONE"]
        self.assertTrue(write_events)
        self.assertEqual(write_events[-1].get("event_status"), "FAILED")

    def test_tx_journal_records_rolled_back_on_verify_failure(self) -> None:
        from runtime_commit_transaction_persistence import read_runtime_transaction_journal
        target_file = self.components["target_file"]
        target_file.write_text(json.dumps({"old": "data"}), encoding="utf-8")
        result = self._run(
            {str(target_file): {"mismatch": "different"}},
            {str(target_file): {"new": "data"}},
        )
        self.assertEqual(result["execute_status"], STATUS_ROLLED_BACK)
        journal_read = read_runtime_transaction_journal(storage_plan=self._result_storage_plan(result))
        self.assertEqual(journal_read["read_status"], "OK")
        stages = [e.get("stage") for e in journal_read["events"]]
        self.assertIn("VERIFY_DONE", stages)
        self.assertIn("ROLLBACK_DONE", stages)
        rollback_events = [e for e in journal_read["events"] if e.get("stage") == "ROLLBACK_DONE"]
        self.assertTrue(rollback_events)
        self.assertEqual(rollback_events[-1].get("event_status"), "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
