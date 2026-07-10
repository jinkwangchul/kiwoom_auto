# -*- coding: utf-8 -*-
"""Tests for runtime_commit_transaction_persistence (M6-13).

All persistence files are written only under tempfile.TemporaryDirectory.
No project runtime/*.json or routines/*/rules.json is touched.
"""

import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from runtime_commit_transaction_contract import (
    TRANSACTION_STAGES,
    build_runtime_commit_transaction_manifest,
    evaluate_runtime_commit_idempotency,
)

from runtime_commit_transaction_persistence import (
    JOURNAL_EVENT_VERSION,
    MANIFEST_WRITE_BLOCKED,
    MANIFEST_WRITE_INVALID,
    MANIFEST_WRITE_UNCHANGED,
    MANIFEST_WRITE_WRITTEN,
    MANIFEST_READ_INVALID,
    MANIFEST_READ_NOT_FOUND,
    MANIFEST_READ_OK,
    JOURNAL_APPEND_APPENDED,
    JOURNAL_APPEND_CONFLICT,
    JOURNAL_APPEND_INVALID,
    JOURNAL_APPEND_UNCHANGED,
    JOURNAL_READ_INVALID,
    JOURNAL_READ_OK,
    SEARCH_INVALID,
    SEARCH_OK,
    SEARCH_PARTIAL,
    STORAGE_STATUS_BLOCKED,
    STORAGE_STATUS_INVALID,
    STORAGE_STATUS_READY,
    append_runtime_transaction_journal_event,
    create_runtime_transaction_storage_plan,
    find_runtime_transaction_records,
    read_runtime_commit_manifest,
    read_runtime_transaction_manifest,
    read_runtime_transaction_journal,
    write_runtime_transaction_manifest,
)


def _make_manifest(commit_id, target_paths=None, metadata=None):
    return build_runtime_commit_transaction_manifest(
        commit_id=commit_id,
        target_paths=target_paths or ["runtime/order_executions.json"],
        execution_plan_hash="exec-hash-001",
        approval_token_id="token-001",
        expected_payload_hash="payload-hash-001",
        backup_plan_hash="backup-hash-001",
        rollback_plan_hash="rollback-hash-001",
        metadata=metadata,
    )


def _make_plan(storage_root, commit_id, transaction_id):
    return create_runtime_transaction_storage_plan(
        storage_root=storage_root,
        commit_id=commit_id,
        transaction_id=transaction_id,
    )


def _base_event(transaction_id, commit_id, sequence, stage, event_status):
    return {
        "event_version": JOURNAL_EVENT_VERSION,
        "transaction_id": transaction_id,
        "commit_id": commit_id,
        "event_id": "",  # filled by persistence
        "stage": stage,
        "event_status": event_status,
        "sequence": sequence,
        "created_at": "2026-07-10T00:00:00",
        "details": {"note": "test"},
        "safety_flags": {
            "runtime_write": False,
            "file_write_called": False,
            "backup_created": False,
            "rollback_executed": False,
            "token_consumed": False,
            "lock_acquired": False,
            "lock_released": False,
            "journal_written": False,
            "manifest_persisted": False,
            "gui_update_called": False,
            "send_order_called": False,
            "chejan_called": False,
            "broker_called": False,
            "sqlite_write": False,
            "rules_write": False,
            "actual_execution": False,
        },
    }


class TestStoragePlan(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_13_plan_"))
        self.commit_id = "commit-abc"
        self.manifest = _make_manifest(self.commit_id)
        self.transaction_id = self.manifest["transaction_id"]

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

    def test_01_normal_storage_plan(self):
        plan = _make_plan(str(self.tmp), self.commit_id, self.transaction_id)
        self.assertEqual(plan["storage_status"], STORAGE_STATUS_READY)
        self.assertTrue(plan["manifest_path"].endswith("manifest.json"))
        self.assertTrue(plan["journal_path"].endswith("journal.jsonl"))
        self.assertEqual(plan["commit_id"], self.commit_id)
        self.assertEqual(plan["transaction_id"], self.transaction_id)
        self.assertTrue(plan["preview_only"])
        for flag, val in plan["safety_flags"].items():
            self.assertFalse(val)

    def test_02_storage_root_missing_invalid(self):
        plan = create_runtime_transaction_storage_plan(
            storage_root="", commit_id=self.commit_id, transaction_id=self.transaction_id
        )
        self.assertEqual(plan["storage_status"], STORAGE_STATUS_INVALID)

    def test_03_commit_id_missing_invalid(self):
        plan = _make_plan(str(self.tmp), "", self.transaction_id)
        self.assertEqual(plan["storage_status"], STORAGE_STATUS_INVALID)

    def test_04_transaction_id_missing_invalid(self):
        plan = _make_plan(str(self.tmp), self.commit_id, "")
        self.assertEqual(plan["storage_status"], STORAGE_STATUS_INVALID)

    def test_05_path_traversal_invalid(self):
        plan = _make_plan(str(self.tmp), self.commit_id, "..")
        self.assertEqual(plan["storage_status"], STORAGE_STATUS_INVALID)

    def test_06_storage_root_escape_blocked(self):
        plan = _make_plan(str(self.tmp), self.commit_id, "../escape")
        self.assertEqual(plan["storage_status"], STORAGE_STATUS_INVALID)

    def test_project_runtime_storage_root_blocked(self):
        project_runtime = (Path(__file__).resolve().parent.parent / "runtime").resolve(strict=False)
        plan = _make_plan(str(project_runtime), self.commit_id, self.transaction_id)
        self.assertEqual(plan["storage_status"], STORAGE_STATUS_BLOCKED)


class TestManifestWrite(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_13_man_"))
        self.commit_id = "commit-man"
        self.manifest = _make_manifest(self.commit_id)
        self.transaction_id = self.manifest["transaction_id"]
        self.plan = _make_plan(str(self.tmp), self.commit_id, self.transaction_id)

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

    def test_07_normal_manifest_write(self):
        result = write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)
        self.assertEqual(result["write_status"], MANIFEST_WRITE_WRITTEN)
        self.assertTrue(result["manifest_written"])
        self.assertTrue(result["file_write_called"])
        self.assertFalse(result["runtime_write"])

    def test_08_manifest_file_created(self):
        write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)
        self.assertTrue(Path(self.plan["manifest_path"]).exists())

    def test_09_manifest_json_deterministic(self):
        write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)
        first = Path(self.plan["manifest_path"]).read_text(encoding="utf-8")
        # Re-create plan + manifest (fresh) and write again to a second location.
        tmp2 = Path(tempfile.mkdtemp(prefix="m6_13_det_"))
        plan2 = _make_plan(str(tmp2), self.commit_id, self.transaction_id)
        write_runtime_transaction_manifest(storage_plan=plan2, manifest=self.manifest)
        second = Path(plan2["manifest_path"]).read_text(encoding="utf-8")
        self.assertEqual(first, second)
        shutil.rmtree(tmp2, ignore_errors=True)

    def test_10_manifest_input_not_mutated(self):
        snapshot = json.dumps(self.manifest, sort_keys=True)
        write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)
        self.assertEqual(json.dumps(self.manifest, sort_keys=True), snapshot)

    def test_11_same_manifest_rewrite_unchanged(self):
        write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)
        result = write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)
        self.assertEqual(result["write_status"], MANIFEST_WRITE_UNCHANGED)
        self.assertFalse(result["manifest_written"])

    def test_12_different_manifest_overwrite_blocked(self):
        write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)
        # Place a different-but-valid manifest (same transaction_id, changed metadata)
        # directly on disk, then attempt to rewrite the original manifest.
        different = dict(self.manifest)
        different["transaction_status"] = "IN_PROGRESS"
        different["transaction_id"] = self.transaction_id
        different["commit_id"] = self.commit_id
        Path(self.plan["manifest_path"]).write_text(
            json.dumps(different, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)
        self.assertEqual(result["write_status"], MANIFEST_WRITE_BLOCKED)

    def test_13_commit_id_mismatch_invalid(self):
        bad = dict(self.manifest)
        bad["commit_id"] = "different-commit"
        result = write_runtime_transaction_manifest(storage_plan=self.plan, manifest=bad)
        self.assertEqual(result["write_status"], MANIFEST_WRITE_INVALID)

    def test_14_transaction_id_mismatch_invalid(self):
        bad = dict(self.manifest)
        bad["transaction_id"] = "different-tx"
        result = write_runtime_transaction_manifest(storage_plan=self.plan, manifest=bad)
        self.assertEqual(result["write_status"], MANIFEST_WRITE_INVALID)

    def test_15_nan_storage_blocked(self):
        bad = _make_manifest(self.commit_id)
        bad["metadata"] = {"bad": math.nan}
        result = write_runtime_transaction_manifest(storage_plan=self.plan, manifest=bad)
        self.assertEqual(result["write_status"], MANIFEST_WRITE_INVALID)


class TestManifestRead(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_13_mr_"))
        self.commit_id = "commit-mr"
        self.manifest = _make_manifest(self.commit_id)
        self.transaction_id = self.manifest["transaction_id"]
        self.plan = _make_plan(str(self.tmp), self.commit_id, self.transaction_id)
        write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)

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

    def test_16_normal_manifest_read(self):
        result = read_runtime_commit_manifest(storage_plan=self.plan)
        self.assertEqual(result["read_status"], MANIFEST_READ_OK)
        self.assertIsNotNone(result["manifest"])
        self.assertTrue(result["manifest_hash"])

    def test_17_manifest_not_found(self):
        empty_plan = _make_plan(str(self.tmp), "other-commit", "other-tx")
        result = read_runtime_commit_manifest(storage_plan=empty_plan)
        self.assertEqual(result["read_status"], MANIFEST_READ_NOT_FOUND)

    def test_18_manifest_corrupted_invalid(self):
        Path(self.plan["manifest_path"]).write_text("not json {", encoding="utf-8")
        result = read_runtime_commit_manifest(storage_plan=self.plan)
        self.assertEqual(result["read_status"], MANIFEST_READ_INVALID)


class TestJournalAppend(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_13_ja_"))
        self.commit_id = "commit-ja"
        self.manifest = _make_manifest(self.commit_id)
        self.transaction_id = self.manifest["transaction_id"]
        self.plan = _make_plan(str(self.tmp), self.commit_id, self.transaction_id)
        write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)

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

    def _append(self, sequence, stage, event_status):
        event = _base_event(self.transaction_id, self.commit_id, sequence, stage, event_status)
        return append_runtime_transaction_journal_event(storage_plan=self.plan, event=event), event

    def test_19_first_append(self):
        result, _ = self._append(1, "MANIFEST_CREATED", "RECORDED")
        self.assertEqual(result["append_status"], JOURNAL_APPEND_APPENDED)
        self.assertTrue(result["journal_written"])
        self.assertTrue(result["file_write_called"])

    def test_20_sequence_one(self):
        result, _ = self._append(1, "MANIFEST_CREATED", "RECORDED")
        self.assertEqual(result["sequence"], 1)

    def test_21_second_append_sequence_two(self):
        self._append(1, "MANIFEST_CREATED", "RECORDED")
        result, _ = self._append(2, "LOCK_PENDING", "STARTED")
        self.assertEqual(result["sequence"], 2)

    def test_22_event_id_deterministic(self):
        r1, _ = self._append(1, "MANIFEST_CREATED", "RECORDED")
        # Re-append same logical event (fresh event dict) -> UNCHANGED, same id.
        r2, _ = self._append(1, "MANIFEST_CREATED", "RECORDED")
        self.assertEqual(r1["event_id"], r2["event_id"])

    def test_23_same_event_reappend_unchanged(self):
        self._append(1, "MANIFEST_CREATED", "RECORDED")
        result, _ = self._append(1, "MANIFEST_CREATED", "RECORDED")
        self.assertEqual(result["append_status"], JOURNAL_APPEND_UNCHANGED)

    def test_24_same_sequence_different_event_conflict(self):
        self._append(1, "MANIFEST_CREATED", "RECORDED")
        result, _ = self._append(1, "LOCK_PENDING", "STARTED")
        self.assertEqual(result["append_status"], JOURNAL_APPEND_CONFLICT)

    def test_25_sequence_skip_invalid(self):
        self._append(1, "MANIFEST_CREATED", "RECORDED")
        result, _ = self._append(3, "LOCK_PENDING", "STARTED")
        self.assertEqual(result["append_status"], JOURNAL_APPEND_INVALID)

    def test_26_journal_input_not_mutated(self):
        event = _base_event(self.transaction_id, self.commit_id, 1, "MANIFEST_CREATED", "RECORDED")
        snapshot = json.dumps(event, sort_keys=True)
        append_runtime_transaction_journal_event(storage_plan=self.plan, event=event)
        self.assertEqual(json.dumps(event, sort_keys=True), snapshot)

    def test_32_transaction_id_mismatch_blocked(self):
        event = _base_event("wrong-tx", self.commit_id, 1, "MANIFEST_CREATED", "RECORDED")
        result = append_runtime_transaction_journal_event(storage_plan=self.plan, event=event)
        self.assertEqual(result["append_status"], JOURNAL_APPEND_INVALID)

    def test_33_commit_id_mismatch_blocked(self):
        event = _base_event(self.transaction_id, "wrong-commit", 1, "MANIFEST_CREATED", "RECORDED")
        result = append_runtime_transaction_journal_event(storage_plan=self.plan, event=event)
        self.assertEqual(result["append_status"], JOURNAL_APPEND_INVALID)

    def test_34_unknown_stage_blocked(self):
        event = _base_event(self.transaction_id, self.commit_id, 1, "UNKNOWN_STAGE", "RECORDED")
        result = append_runtime_transaction_journal_event(storage_plan=self.plan, event=event)
        self.assertEqual(result["append_status"], JOURNAL_APPEND_INVALID)

    def test_35_unknown_event_status_blocked(self):
        event = _base_event(self.transaction_id, self.commit_id, 1, "MANIFEST_CREATED", "UNKNOWN_STATUS")
        result = append_runtime_transaction_journal_event(storage_plan=self.plan, event=event)
        self.assertEqual(result["append_status"], JOURNAL_APPEND_INVALID)


class TestJournalRead(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_13_jr_"))
        self.commit_id = "commit-jr"
        self.manifest = _make_manifest(self.commit_id)
        self.transaction_id = self.manifest["transaction_id"]
        self.plan = _make_plan(str(self.tmp), self.commit_id, self.transaction_id)
        write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)

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

    def _append(self, sequence, stage, event_status):
        event = _base_event(self.transaction_id, self.commit_id, sequence, stage, event_status)
        return append_runtime_transaction_journal_event(storage_plan=self.plan, event=event)

    def test_27_normal_journal_read(self):
        self._append(1, "MANIFEST_CREATED", "RECORDED")
        self._append(2, "LOCK_PENDING", "STARTED")
        result = read_runtime_transaction_journal(storage_plan=self.plan)
        self.assertEqual(result["read_status"], JOURNAL_READ_OK)
        self.assertEqual(result["event_count"], 2)
        self.assertEqual(result["last_sequence"], 2)

    def test_28_sequence_continuity(self):
        self._append(1, "MANIFEST_CREATED", "RECORDED")
        self._append(2, "LOCK_PENDING", "STARTED")
        result = read_runtime_transaction_journal(storage_plan=self.plan)
        seqs = [e["sequence"] for e in result["events"]]
        self.assertEqual(seqs, [1, 2])

    def test_29_duplicate_sequence_blocked(self):
        self._append(1, "MANIFEST_CREATED", "RECORDED")
        # Manually write a second line with duplicate sequence 1.
        with open(self.plan["journal_path"], "a", encoding="utf-8") as fh:
            dup = _base_event(self.transaction_id, self.commit_id, 1, "LOCK_PENDING", "STARTED")
            dup["event_id"] = "manual-dup-id"
            fh.write(json.dumps(dup, ensure_ascii=False) + "\n")
        result = read_runtime_transaction_journal(storage_plan=self.plan)
        self.assertEqual(result["read_status"], JOURNAL_READ_INVALID)

    def test_30_partial_last_line_blocked(self):
        self._append(1, "MANIFEST_CREATED", "RECORDED")
        with open(self.plan["journal_path"], "a", encoding="utf-8") as fh:
            fh.write("{partial-no-newline")
        result = read_runtime_transaction_journal(storage_plan=self.plan)
        self.assertEqual(result["read_status"], JOURNAL_READ_INVALID)

    def test_31_invalid_json_line_blocked(self):
        self._append(1, "MANIFEST_CREATED", "RECORDED")
        with open(self.plan["journal_path"], "a", encoding="utf-8") as fh:
            fh.write("not-json-line\n")
        result = read_runtime_transaction_journal(storage_plan=self.plan)
        self.assertEqual(result["read_status"], JOURNAL_READ_INVALID)


class TestRecordSearch(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_13_find_"))
        self.commit_id = "commit-find"
        self.manifest = _make_manifest(self.commit_id)
        self.transaction_id = self.manifest["transaction_id"]
        self.target_set_hash = self.manifest["target_set_hash"]
        self.plan = _make_plan(str(self.tmp), self.commit_id, self.transaction_id)
        write_runtime_transaction_manifest(storage_plan=self.plan, manifest=self.manifest)
        append_runtime_transaction_journal_event(
            storage_plan=self.plan,
            event=_base_event(self.transaction_id, self.commit_id, 1, "MANIFEST_CREATED", "RECORDED"),
        )

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

    def test_36_normal_record_search(self):
        result = find_runtime_transaction_records(storage_root=str(self.tmp))
        self.assertEqual(result["search_status"], SEARCH_OK)
        self.assertEqual(result["record_count"], 1)
        rec = result["records"][0]
        self.assertEqual(rec["transaction_id"], self.transaction_id)
        self.assertEqual(rec["commit_id"], self.commit_id)
        self.assertEqual(rec["target_set_hash"], self.target_set_hash)
        self.assertEqual(rec["last_sequence"], 1)
        self.assertEqual(rec["last_journal_stage"], "MANIFEST_CREATED")
        self.assertFalse(rec["lock_active"])
        self.assertTrue(rec["record_valid"])

    def test_37_commit_id_filter(self):
        result = find_runtime_transaction_records(storage_root=str(self.tmp), commit_id=self.commit_id)
        self.assertEqual(result["record_count"], 1)
        result2 = find_runtime_transaction_records(storage_root=str(self.tmp), commit_id="nope")
        self.assertEqual(result2["record_count"], 0)

    def test_38_target_set_hash_filter(self):
        result = find_runtime_transaction_records(storage_root=str(self.tmp), target_set_hash=self.target_set_hash)
        self.assertEqual(result["record_count"], 1)
        result2 = find_runtime_transaction_records(storage_root=str(self.tmp), target_set_hash="nope")
        self.assertEqual(result2["record_count"], 0)

    def test_39_transaction_id_filter(self):
        result = find_runtime_transaction_records(storage_root=str(self.tmp), transaction_id=self.transaction_id)
        self.assertEqual(result["record_count"], 1)
        result2 = find_runtime_transaction_records(storage_root=str(self.tmp), transaction_id="nope")
        self.assertEqual(result2["record_count"], 0)

    def test_40_corrupt_record_partial(self):
        # Create a second transaction dir with a corrupt manifest.
        corrupt_dir = self.tmp / "transactions" / "corrupt-tx"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / "manifest.json").write_text("broken {", encoding="utf-8")
        result = find_runtime_transaction_records(storage_root=str(self.tmp))
        self.assertEqual(result["search_status"], SEARCH_PARTIAL)
        self.assertEqual(result["record_count"], 2)
        invalid = [r for r in result["records"] if not r["record_valid"]]
        self.assertEqual(len(invalid), 1)

    def test_41_idempotency_api_compatible(self):
        result = find_runtime_transaction_records(storage_root=str(self.tmp))
        rec = result["records"][0]
        idem = evaluate_runtime_commit_idempotency(
            commit_id=rec["commit_id"],
            target_set_hash=rec["target_set_hash"],
            transaction_state=rec["transaction_status"],
            existing_records=result["records"],
        )
        self.assertIn("idempotency_status", idem)
        self.assertEqual(idem["commit_id"], rec["commit_id"])

    def test_42_manifest_read_compat_api_exists(self):
        # New compatibility API must exist and behave identically.
        result_new = read_runtime_transaction_manifest(storage_plan=self.plan)
        result_old = read_runtime_commit_manifest(storage_plan=self.plan)
        self.assertEqual(result_new["read_status"], MANIFEST_READ_OK)
        self.assertEqual(result_new["read_status"], result_old["read_status"])
        self.assertEqual(result_new["manifest_hash"], result_old["manifest_hash"])
        self.assertEqual(result_new["manifest"], result_old["manifest"])


if __name__ == "__main__":
    unittest.main()
