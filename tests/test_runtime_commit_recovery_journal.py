# -*- coding: utf-8 -*-
"""Tests for runtime_commit_recovery_journal (M6-17).

All journal files are written only under tempfile.TemporaryDirectory.
No project runtime/*.json or routines/*/rules.json is touched.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from runtime_commit_recovery_journal import (
    APPEND_APPENDED,
    APPEND_CLOSED,
    APPEND_INVALID,
    CREATE_INVALID,
    CREATE_OK,
    READ_INVALID,
    READ_OK,
    RECORD_CLOSED,
    RECORD_INVALID,
    RECORD_OK,
    RECORD_UNCHANGED,
    RECOVERY_STATUS_ABORTED,
    RECOVERY_STATUS_COMPLETED,
    RECOVERY_STATUS_MANUAL_RESTORE_REQUIRED,
    RECOVERY_STATUS_ROLLED_BACK,
    STAGE_STATUS_FAILED,
    STAGE_STATUS_STARTED,
    STAGE_STATUS_SUCCEEDED,
    append_recovery_stage,
    create_recovery_journal,
    get_last_recovery_stage,
    get_recovery_status,
    record_recovery_status,
)


def _create_journal(transaction_id="tx-1", commit_id="commit-1", owner_id="executor",
                    storage_root=None):
    return create_recovery_journal(
        transaction_id=transaction_id,
        commit_id=commit_id,
        owner_id=owner_id,
        storage_root=storage_root,
    )


class TestJournalCreation(unittest.TestCase):
    def test_create_ok_default_tempfile(self):
        journal = _create_journal()
        self.assertEqual(journal["create_status"], CREATE_OK)
        self.assertTrue(journal["journal_path"])
        self.assertFalse(journal["closed"])
        # Journal path must live under a temp directory.
        self.assertIn("rj_", journal["storage_root"])

    def test_create_ok_explicit_storage_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = _create_journal(storage_root=tmp)
            self.assertEqual(journal["create_status"], CREATE_OK)
            self.assertTrue(journal["journal_path"].startswith(tmp))

    def test_create_invalid_empty_transaction_id(self):
        journal = create_recovery_journal(transaction_id="", commit_id="commit-1")
        self.assertEqual(journal["create_status"], CREATE_INVALID)
        self.assertIn("transaction_id must be a non-empty string", journal["issues"])

    def test_create_invalid_empty_commit_id(self):
        journal = create_recovery_journal(transaction_id="tx-1", commit_id="")
        self.assertEqual(journal["create_status"], CREATE_INVALID)
        self.assertIn("commit_id must be a non-empty string", journal["issues"])

    def test_create_invalid_path_separator(self):
        journal = create_recovery_journal(transaction_id="tx/1", commit_id="commit-1")
        self.assertEqual(journal["create_status"], CREATE_INVALID)

    def test_create_rejects_project_runtime(self):
        runtime_dir = os.path.join(os.path.dirname(__file__), "..", "runtime")
        journal = create_recovery_journal(
            transaction_id="tx-1", commit_id="commit-1", storage_root=runtime_dir
        )
        self.assertEqual(journal["create_status"], CREATE_INVALID)
        self.assertIn("project runtime path is not allowed as storage_root", journal["issues"])


class TestStageAppend(unittest.TestCase):
    def test_append_stage_sequence_increments(self):
        journal = _create_journal()
        r1 = append_recovery_stage(journal=journal, stage="PREPARE", status=STAGE_STATUS_STARTED)
        self.assertEqual(r1["append_status"], APPEND_APPENDED)
        self.assertEqual(r1["sequence"], 1)
        r2 = append_recovery_stage(journal=journal, stage="EXECUTE", status=STAGE_STATUS_SUCCEEDED)
        self.assertEqual(r2["append_status"], APPEND_APPENDED)
        self.assertEqual(r2["sequence"], 2)
        self.assertTrue(r2["journal_written"])
        self.assertFalse(r2["runtime_write"])
        self.assertFalse(r2["actual_execution"])

    def test_append_invalid_status(self):
        journal = _create_journal()
        r = append_recovery_stage(journal=journal, stage="PREPARE", status="BOGUS")
        self.assertEqual(r["append_status"], APPEND_INVALID)

    def test_append_invalid_empty_stage(self):
        journal = _create_journal()
        r = append_recovery_stage(journal=journal, stage="", status=STAGE_STATUS_STARTED)
        self.assertEqual(r["append_status"], APPEND_INVALID)

    def test_append_invalid_reserved_stage(self):
        journal = _create_journal()
        r = append_recovery_stage(journal=journal, stage="__RECOVERY_STATUS__",
                                  status=STAGE_STATUS_STARTED)
        self.assertEqual(r["append_status"], APPEND_INVALID)

    def test_append_invalid_handle(self):
        r = append_recovery_stage(journal={}, stage="PREPARE", status=STAGE_STATUS_STARTED)
        self.assertEqual(r["append_status"], APPEND_INVALID)

    def test_append_failed_status_allowed(self):
        journal = _create_journal()
        r = append_recovery_stage(journal=journal, stage="EXECUTE", status=STAGE_STATUS_FAILED)
        self.assertEqual(r["append_status"], APPEND_APPENDED)
        self.assertEqual(r["status"], STAGE_STATUS_FAILED)


class TestLastStage(unittest.TestCase):
    def test_last_stage_none_when_empty(self):
        journal = _create_journal()
        r = get_last_recovery_stage(journal=journal)
        self.assertEqual(r["read_status"], READ_OK)
        self.assertFalse(r["found"])

    def test_last_stage_returns_most_recent(self):
        journal = _create_journal()
        append_recovery_stage(journal=journal, stage="PREPARE", status=STAGE_STATUS_STARTED)
        append_recovery_stage(journal=journal, stage="EXECUTE", status=STAGE_STATUS_SUCCEEDED)
        r = get_last_recovery_stage(journal=journal)
        self.assertEqual(r["read_status"], READ_OK)
        self.assertTrue(r["found"])
        self.assertEqual(r["last_stage"], "EXECUTE")
        self.assertEqual(r["status"], STAGE_STATUS_SUCCEEDED)
        self.assertEqual(r["sequence"], 2)

    def test_last_stage_invalid_handle(self):
        r = get_last_recovery_stage(journal={})
        self.assertEqual(r["read_status"], READ_INVALID)


class TestRecoveryStatus(unittest.TestCase):
    def test_record_completed(self):
        journal = _create_journal()
        append_recovery_stage(journal=journal, stage="PREPARE", status=STAGE_STATUS_STARTED)
        r = record_recovery_status(journal=journal, recovery_status=RECOVERY_STATUS_COMPLETED)
        self.assertEqual(r["record_status"], RECORD_OK)
        self.assertTrue(r["closed"])
        self.assertTrue(r["journal_written"])
        self.assertFalse(r["actual_execution"])

    def test_record_all_terminal_statuses(self):
        for status in (
            RECOVERY_STATUS_ABORTED,
            RECOVERY_STATUS_ROLLED_BACK,
            RECOVERY_STATUS_MANUAL_RESTORE_REQUIRED,
        ):
            journal = _create_journal(transaction_id=f"tx-{status}", commit_id=f"c-{status}")
            r = record_recovery_status(journal=journal, recovery_status=status)
            self.assertEqual(r["record_status"], RECORD_OK, status)
            self.assertEqual(r["recovery_status"], status)

    def test_record_invalid_status(self):
        journal = _create_journal()
        r = record_recovery_status(journal=journal, recovery_status="BOGUS")
        self.assertEqual(r["record_status"], RECORD_INVALID)

    def test_record_invalid_handle(self):
        r = record_recovery_status(journal={}, recovery_status=RECOVERY_STATUS_COMPLETED)
        self.assertEqual(r["record_status"], RECORD_INVALID)

    def test_append_blocked_after_close(self):
        journal = _create_journal()
        record_recovery_status(journal=journal, recovery_status=RECOVERY_STATUS_COMPLETED)
        r = append_recovery_stage(journal=journal, stage="CLEANUP", status=STAGE_STATUS_STARTED)
        self.assertEqual(r["append_status"], APPEND_CLOSED)

    def test_record_closed_different_status(self):
        journal = _create_journal()
        record_recovery_status(journal=journal, recovery_status=RECOVERY_STATUS_COMPLETED)
        r = record_recovery_status(journal=journal, recovery_status=RECOVERY_STATUS_ABORTED)
        self.assertEqual(r["record_status"], RECORD_CLOSED)

    def test_record_idempotent_same_status(self):
        journal = _create_journal()
        r1 = record_recovery_status(journal=journal, recovery_status=RECOVERY_STATUS_ABORTED)
        self.assertEqual(r1["record_status"], RECORD_OK)
        r2 = record_recovery_status(journal=journal, recovery_status=RECOVERY_STATUS_ABORTED)
        self.assertEqual(r2["record_status"], RECORD_UNCHANGED)


class TestRecoveryStatusQuery(unittest.TestCase):
    def test_status_in_progress_when_open(self):
        journal = _create_journal()
        r = get_recovery_status(journal=journal)
        self.assertEqual(r["read_status"], READ_OK)
        self.assertEqual(r["recovery_status"], "IN_PROGRESS")
        self.assertFalse(r["closed"])

    def test_status_reflects_recorded(self):
        journal = _create_journal()
        record_recovery_status(journal=journal, recovery_status=RECOVERY_STATUS_ROLLED_BACK)
        r = get_recovery_status(journal=journal)
        self.assertEqual(r["read_status"], READ_OK)
        self.assertEqual(r["recovery_status"], RECOVERY_STATUS_ROLLED_BACK)
        self.assertTrue(r["closed"])
        self.assertTrue(r["event_id"])

    def test_status_invalid_handle(self):
        r = get_recovery_status(journal={})
        self.assertEqual(r["read_status"], READ_INVALID)


class TestAppendOnlyPolicy(unittest.TestCase):
    def test_journal_file_is_jsonl_append_only(self):
        journal = _create_journal()
        append_recovery_stage(journal=journal, stage="PREPARE", status=STAGE_STATUS_STARTED)
        append_recovery_stage(journal=journal, stage="EXECUTE", status=STAGE_STATUS_SUCCEEDED)
        record_recovery_status(journal=journal, recovery_status=RECOVERY_STATUS_COMPLETED)

        path = Path(journal["journal_path"])
        self.assertTrue(path.exists())
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 3)
        for line in lines:
            obj = json.loads(line)
            self.assertIn("sequence", obj)
            self.assertIn("event_id", obj)
        # Sequences must be 1, 2, 3 in order (append-only, no gaps).
        sequences = [json.loads(line)["sequence"] for line in lines]
        self.assertEqual(sequences, [1, 2, 3])
        # Last entry is terminal.
        self.assertTrue(json.loads(lines[-1])["is_terminal"])

    def test_no_runtime_or_routines_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = _create_journal(storage_root=tmp)
            append_recovery_stage(journal=journal, stage="PREPARE", status=STAGE_STATUS_STARTED)
            record_recovery_status(journal=journal, recovery_status=RECOVERY_STATUS_COMPLETED)
            # Only the journal file should exist under recovery_journal dir.
            journal_dir = Path(journal["journal_dir"])
            files = list(journal_dir.iterdir())
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].name.endswith(".journal.jsonl"))


if __name__ == "__main__":
    unittest.main()