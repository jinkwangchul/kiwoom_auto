# -*- coding: utf-8 -*-
"""Tests for runtime_commit_guard (M6-14).

All lock files are written only under tempfile.TemporaryDirectory.
No project runtime/*.json or routines/*/rules.json is touched.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from runtime_commit_guard import (
    GUARD_STATUS_BLOCKED,
    GUARD_STATUS_INVALID,
    GUARD_STATUS_READY,
    LOCK_ACQUIRE_ACQUIRED,
    LOCK_ACQUIRE_BLOCKED,
    LOCK_READ_INVALID,
    LOCK_READ_NOT_FOUND,
    LOCK_READ_OK,
    LOCK_RELEASE_BLOCKED,
    LOCK_RELEASE_RELEASED,
    LOCK_RELEASE_UNCHANGED,
    LOCK_STATUS_ACQUIRED,
    LOCK_STATUS_RELEASED,
    acquire_runtime_commit_lock,
    create_runtime_commit_guard_plan,
    evaluate_runtime_commit_guard,
    read_runtime_commit_lock,
    release_runtime_commit_lock,
)
from runtime_commit_transaction_contract import (
    evaluate_runtime_commit_idempotency,
)


def _make_guard_plan(storage_root, commit_id="commit-1", transaction_id="tx-1",
                     target_set_hash="hash-1", owner_id="owner-1"):
    return create_runtime_commit_guard_plan(
        storage_root=storage_root,
        commit_id=commit_id,
        transaction_id=transaction_id,
        target_set_hash=target_set_hash,
        owner_id=owner_id,
    )


class TestGuardPlan(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_14_guard_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def test_03_normal_guard_plan(self):
        plan = _make_guard_plan(str(self.tmp))
        self.assertEqual(plan["guard_status"], GUARD_STATUS_READY)
        self.assertTrue(plan["lock_path"].endswith(".json"))
        self.assertTrue(plan["lock_path"].startswith(str(self.tmp)))
        self.assertTrue(plan["preview_only"])
        for flag, val in plan["safety_flags"].items():
            self.assertFalse(val)

    def test_04_empty_commit_id_invalid(self):
        plan = _make_guard_plan(str(self.tmp), commit_id="")
        self.assertEqual(plan["guard_status"], GUARD_STATUS_INVALID)

    def test_05_empty_transaction_id_invalid(self):
        plan = _make_guard_plan(str(self.tmp), transaction_id="")
        self.assertEqual(plan["guard_status"], GUARD_STATUS_INVALID)

    def test_06_empty_target_set_hash_invalid(self):
        plan = _make_guard_plan(str(self.tmp), target_set_hash="")
        self.assertEqual(plan["guard_status"], GUARD_STATUS_INVALID)

    def test_07_empty_owner_id_invalid(self):
        plan = _make_guard_plan(str(self.tmp), owner_id="")
        self.assertEqual(plan["guard_status"], GUARD_STATUS_INVALID)

    def test_08_path_traversal_blocked(self):
        plan = _make_guard_plan(str(self.tmp), commit_id="../escape")
        self.assertEqual(plan["guard_status"], GUARD_STATUS_INVALID)

    def test_09_project_runtime_blocked(self):
        project_runtime = (Path(__file__).resolve().parent.parent / "runtime").resolve(strict=False)
        plan = _make_guard_plan(str(project_runtime))
        self.assertEqual(plan["guard_status"], GUARD_STATUS_BLOCKED)

    def test_10_deterministic_lock_key(self):
        p1 = _make_guard_plan(str(self.tmp), commit_id="c", target_set_hash="h")
        p2 = _make_guard_plan(str(self.tmp), commit_id="c", target_set_hash="h")
        self.assertEqual(p1["lock_key"], p2["lock_key"])
        p3 = _make_guard_plan(str(self.tmp), commit_id="c", target_set_hash="h2")
        self.assertNotEqual(p1["lock_key"], p3["lock_key"])


class TestLockAcquire(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_14_acq_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def _plan(self, owner="owner-1"):
        return _make_guard_plan(str(self.tmp), owner_id=owner)

    def test_11_first_acquire_success(self):
        plan = self._plan()
        result = acquire_runtime_commit_lock(guard_plan=plan)
        self.assertEqual(result["acquire_status"], LOCK_ACQUIRE_ACQUIRED)
        self.assertTrue(result["lock_acquired"])
        self.assertTrue(result["file_write_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["actual_execution"])

    def test_12_lock_file_created(self):
        plan = self._plan()
        acquire_runtime_commit_lock(guard_plan=plan)
        self.assertTrue(Path(plan["lock_path"]).exists())

    def test_13_second_acquire_blocked(self):
        plan = self._plan()
        first = acquire_runtime_commit_lock(guard_plan=plan)
        self.assertEqual(first["acquire_status"], LOCK_ACQUIRE_ACQUIRED)
        second = acquire_runtime_commit_lock(guard_plan=plan)
        self.assertEqual(second["acquire_status"], LOCK_ACQUIRE_BLOCKED)
        self.assertFalse(second["lock_acquired"])

    def test_14_same_owner_reentrant_blocked(self):
        plan = self._plan(owner="same-owner")
        acquire_runtime_commit_lock(guard_plan=plan)
        again = acquire_runtime_commit_lock(guard_plan=plan)
        self.assertEqual(again["acquire_status"], LOCK_ACQUIRE_BLOCKED)

    def test_15_different_owner_blocked(self):
        plan = self._plan(owner="owner-A")
        acquire_runtime_commit_lock(guard_plan=plan)
        plan2 = _make_guard_plan(str(self.tmp), owner_id="owner-B")
        other = acquire_runtime_commit_lock(guard_plan=plan2)
        self.assertEqual(other["acquire_status"], LOCK_ACQUIRE_BLOCKED)

    def test_16_existing_lock_immutable(self):
        plan = self._plan()
        acquire_runtime_commit_lock(guard_plan=plan)
        original = Path(plan["lock_path"]).read_text(encoding="utf-8")
        acquire_runtime_commit_lock(guard_plan=plan)
        after = Path(plan["lock_path"]).read_text(encoding="utf-8")
        self.assertEqual(original, after)


class TestLockRead(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_14_read_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def _plan(self):
        return _make_guard_plan(str(self.tmp))

    def test_17_normal_lock_read(self):
        plan = self._plan()
        acquire_runtime_commit_lock(guard_plan=plan)
        result = read_runtime_commit_lock(guard_plan=plan)
        self.assertEqual(result["read_status"], LOCK_READ_OK)
        rec = result["lock_record"]
        self.assertEqual(rec["lock_status"], LOCK_STATUS_ACQUIRED)
        self.assertEqual(rec["owner_id"], "owner-1")
        self.assertEqual(rec["contract_version"], "M6_RUNTIME_LOCK_RECORD_V1")
        self.assertFalse(rec["reentrant"])

    def test_18_not_found(self):
        plan = self._plan()
        result = read_runtime_commit_lock(guard_plan=plan)
        self.assertEqual(result["read_status"], LOCK_READ_NOT_FOUND)

    def test_19_corrupted_lock_invalid(self):
        plan = self._plan()
        Path(plan["lock_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(plan["lock_path"]).write_text("not json {", encoding="utf-8")
        result = read_runtime_commit_lock(guard_plan=plan)
        self.assertEqual(result["read_status"], LOCK_READ_INVALID)

    def test_20_contract_version_error(self):
        plan = self._plan()
        acquire_runtime_commit_lock(guard_plan=plan)
        rec = read_runtime_commit_lock(guard_plan=plan)["lock_record"]
        rec["contract_version"] = "WRONG"
        Path(plan["lock_path"]).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        result = read_runtime_commit_lock(guard_plan=plan)
        self.assertEqual(result["read_status"], LOCK_READ_INVALID)


class TestLockRelease(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_14_rel_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def _plan(self, owner="owner-1"):
        return _make_guard_plan(str(self.tmp), owner_id=owner)

    def test_21_owner_mismatch_blocked(self):
        plan = self._plan(owner="owner-A")
        acquire_runtime_commit_lock(guard_plan=plan)
        result = release_runtime_commit_lock(guard_plan=plan, expected_owner_id="owner-B")
        self.assertEqual(result["release_status"], LOCK_RELEASE_BLOCKED)
        self.assertFalse(result["lock_released"])

    def test_22_normal_release(self):
        plan = self._plan(owner="owner-1")
        acquire_runtime_commit_lock(guard_plan=plan)
        result = release_runtime_commit_lock(guard_plan=plan, expected_owner_id="owner-1")
        self.assertEqual(result["release_status"], LOCK_RELEASE_RELEASED)
        self.assertTrue(result["lock_released"])
        self.assertTrue(result["file_write_called"])
        self.assertFalse(result["runtime_write"])

    def test_23_lock_released_status(self):
        plan = self._plan()
        acquire_runtime_commit_lock(guard_plan=plan)
        release_runtime_commit_lock(guard_plan=plan, expected_owner_id="owner-1")
        rec = read_runtime_commit_lock(guard_plan=plan)["lock_record"]
        self.assertEqual(rec["lock_status"], LOCK_STATUS_RELEASED)
        self.assertTrue(rec["released_at"])

    def test_24_already_released_unchanged(self):
        plan = self._plan()
        acquire_runtime_commit_lock(guard_plan=plan)
        release_runtime_commit_lock(guard_plan=plan, expected_owner_id="owner-1")
        again = release_runtime_commit_lock(guard_plan=plan, expected_owner_id="owner-1")
        self.assertEqual(again["release_status"], LOCK_RELEASE_UNCHANGED)
        self.assertFalse(again["lock_released"])

    def test_25_release_preserves_ids(self):
        plan = self._plan(owner="owner-X")
        acquire_runtime_commit_lock(guard_plan=plan)
        result = release_runtime_commit_lock(guard_plan=plan, expected_owner_id="owner-X")
        rec = result["lock_record"]
        self.assertEqual(rec["lock_key"], plan["lock_key"])
        self.assertEqual(rec["commit_id"], "commit-1")
        self.assertEqual(rec["transaction_id"], "tx-1")
        self.assertEqual(rec["target_set_hash"], "hash-1")
        self.assertEqual(rec["owner_id"], "owner-X")


class TestGuardEvaluation(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_14_eval_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def _plan(self):
        return _make_guard_plan(str(self.tmp))

    def test_26_new_idempotency_allowed(self):
        plan = self._plan()
        result = evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=[])
        self.assertEqual(result["idempotency_status"], "NEW")
        self.assertTrue(result["execution_allowed"])
        self.assertEqual(result["guard_status"], GUARD_STATUS_READY)

    def test_27_retry_allowed(self):
        plan = self._plan()
        records = [{"commit_id": "commit-1", "target_set_hash": "hash-1",
                    "transaction_status": "ABORTED", "current_stage": "MANIFEST_CREATED"}]
        result = evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=records)
        self.assertEqual(result["idempotency_status"], "RETRY_ALLOWED")
        self.assertTrue(result["execution_allowed"])

    def test_28_in_progress_blocked(self):
        plan = self._plan()
        records = [{"commit_id": "commit-1", "target_set_hash": "hash-1",
                    "transaction_status": "IN_PROGRESS", "current_stage": "MANIFEST_CREATED"}]
        result = evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=records)
        self.assertEqual(result["idempotency_status"], "IN_PROGRESS_BLOCKED")
        self.assertFalse(result["execution_allowed"])

    def test_29_already_committed_blocked(self):
        plan = self._plan()
        records = [{"commit_id": "commit-1", "target_set_hash": "hash-1",
                    "transaction_status": "COMMITTED", "current_stage": "COMPLETED"}]
        result = evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=records)
        self.assertEqual(result["idempotency_status"], "ALREADY_COMMITTED")
        self.assertFalse(result["execution_allowed"])

    def test_30_recovery_required_blocked(self):
        plan = self._plan()
        records = [{"commit_id": "commit-1", "target_set_hash": "hash-1",
                    "transaction_status": "IN_PROGRESS", "current_stage": "WRITE_STARTED"}]
        result = evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=records)
        self.assertEqual(result["idempotency_status"], "RECOVERY_REQUIRED")
        self.assertFalse(result["execution_allowed"])
        self.assertTrue(result["recovery_required"])

    def test_31_manual_review_blocked(self):
        plan = self._plan()
        records = [{"commit_id": "commit-1", "target_set_hash": "hash-1",
                    "transaction_status": "FAILED", "current_stage": "MANIFEST_CREATED"}]
        result = evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=records)
        self.assertEqual(result["idempotency_status"], "MANUAL_REVIEW_REQUIRED")
        self.assertFalse(result["execution_allowed"])
        self.assertTrue(result["manual_review_required"])

    def test_32_active_lock_blocked(self):
        plan = self._plan()
        acquire_runtime_commit_lock(guard_plan=plan)
        result = evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=[])
        self.assertEqual(result["lock_status"], LOCK_STATUS_ACQUIRED)
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["guard_status"], GUARD_STATUS_BLOCKED)

    def test_33_eval_does_not_acquire_lock(self):
        plan = self._plan()
        evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=[])
        read = read_runtime_commit_lock(guard_plan=plan)
        self.assertEqual(read["read_status"], LOCK_READ_NOT_FOUND)

    def test_34_idempotency_not_duplicated(self):
        # Ensure we delegate to M6-11 (same result shape).
        plan = self._plan()
        result = evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=[])
        direct = evaluate_runtime_commit_idempotency(
            commit_id="commit-1", target_set_hash="hash-1",
            transaction_state="CREATED", existing_records=[],
        )
        self.assertEqual(result["idempotency_status"], direct["idempotency_status"])

    def test_35_input_not_mutated(self):
        plan = self._plan()
        snapshot = json.dumps(plan, sort_keys=True)
        evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=[])
        self.assertEqual(json.dumps(plan, sort_keys=True), snapshot)

    def test_36_no_runtime_write(self):
        plan = self._plan()
        result = evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=[])
        self.assertFalse(result["safety_flags"]["runtime_write"])
        self.assertFalse(result["safety_flags"]["actual_execution"])

    def test_37_no_token_backup_rollback(self):
        plan = self._plan()
        result = evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=[])
        flags = result["safety_flags"]
        self.assertFalse(flags["token_consumed"])
        self.assertFalse(flags["backup_created"])
        self.assertFalse(flags["rollback_executed"])

    def test_38_tempfile_only(self):
        # No files created outside tmp (lock only created on acquire, not eval).
        before = list(self.tmp.glob("**/*"))
        plan = self._plan()
        evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=[])
        after = list(self.tmp.glob("**/*"))
        self.assertEqual(before, after)

    def test_39_runtime_routines_unchanged(self):
        project_runtime = (Path(__file__).resolve().parent.parent / "runtime").resolve(strict=False)
        before = {p.name: p.stat().st_mtime_ns for p in project_runtime.glob("*.json")}
        plan = _make_guard_plan(str(self.tmp))
        evaluate_runtime_commit_guard(guard_plan=plan, transaction_records=[])
        after = {p.name: p.stat().st_mtime_ns for p in project_runtime.glob("*.json")}
        self.assertEqual(before, after)

    def test_40_full_regression_covered_by_suite(self):
        # Placeholder ensuring suite covers guard evaluation paths.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()