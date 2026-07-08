# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path
import unittest

from execution_runtime_commit_plan_orchestrator import ORCHESTRATOR_TYPE
from lifecycle_commit_service import commit_lifecycle
from lifecycle_commit_writer import LifecycleCommitWriter


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


def _rows(db_path: Path, table: str) -> list[dict[str, object]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
    finally:
        conn.close()


class LifecycleCommitWriterServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "lifecycle.sqlite3"
        self.writer = LifecycleCommitWriter(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _contract(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "contract_type": "ORDER_LIFECYCLE_COMMIT_CONTRACT_PREVIEW",
            "contract_version": "preview-1",
            "preview_only": True,
            "lifecycle_write": False,
            "runtime_write": False,
            "queue_write": False,
            "candidate_lifecycle_event": "ORDER_RECEIVED",
            "evidence_id": "EVIDENCE_WRITER_1",
            "record_id": "RECORD_WRITER_1",
            "order_id": "ORDER_WRITER_1",
            "dispatch_id": "DISPATCH_WRITER_1",
            "source_signal_id": "SIGNAL_WRITER_1",
            "order_queued_id": "ORDER_QUEUED_WRITER_1",
            "target_name": "temp_lifecycle",
            "lifecycle_store": "temp_store",
            "required_next_service": "ORDER_LIFECYCLE_COMMIT_SERVICE",
        }
        result.update(overrides)
        return result

    def _plan(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "plan_type": "ORDER_LIFECYCLE_COMMIT_PLAN_PREVIEW",
            "preview_only": True,
            "lifecycle_write": False,
            "runtime_write": False,
            "queue_write": False,
            "would_append_event": "ORDER_RECEIVED",
        }
        result.update(overrides)
        return result

    def _preview(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "LIFECYCLE_COMMIT_READY",
            "commit_contract": self._contract(),
            "commit_plan": self._plan(),
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "lifecycle_write": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _orchestrator_result(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "orchestrator_type": ORCHESTRATOR_TYPE,
            "status": "READY",
            "commit_ready": True,
            "commit_plan": {
                "planned_records": [{"record_id": "planned-1"}],
                "planned_targets": {},
            },
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_writer_prepare_commit_creates_prepared_transition_and_journal(self) -> None:
        result = self.writer.prepare_commit(self._contract(), self._plan())

        self.assertTrue(result["ok"])
        self.assertTrue(result["prepared"])
        self.assertTrue(result["commit_token"])
        transitions = _rows(self.db_path, "transitions")
        journal = _rows(self.db_path, "journal")
        self.assertEqual(1, len(transitions))
        self.assertEqual("prepared", transitions[0]["status"])
        self.assertEqual("ORDER_WRITER_1", transitions[0]["order_id"])
        self.assertEqual("prepared", journal[0]["action"])

    def test_writer_finalize_commit_marks_committed(self) -> None:
        prepared = self.writer.prepare_commit(self._contract(), self._plan())

        result = self.writer.finalize_commit(prepared["commit_token"], success=True, metadata={"source": "test"})

        self.assertTrue(result["ok"])
        self.assertEqual("committed", result["status"])
        transitions = _rows(self.db_path, "transitions")
        journal_actions = [row["action"] for row in _rows(self.db_path, "journal")]
        self.assertEqual("committed", transitions[0]["status"])
        self.assertIn("committed", journal_actions)

    def test_writer_finalize_commit_marks_aborted(self) -> None:
        prepared = self.writer.prepare_commit(self._contract(), self._plan())

        result = self.writer.finalize_commit(prepared["commit_token"], success=False, metadata={"reason": "external_failed"})

        self.assertTrue(result["ok"])
        self.assertEqual("aborted", result["status"])
        transitions = _rows(self.db_path, "transitions")
        journal_actions = [row["action"] for row in _rows(self.db_path, "journal")]
        self.assertEqual("aborted", transitions[0]["status"])
        self.assertIn("aborted", journal_actions)

    def test_evidence_id_duplicate_is_blocked_after_commit(self) -> None:
        prepared = self.writer.prepare_commit(self._contract(), self._plan())
        self.writer.finalize_commit(prepared["commit_token"], success=True)

        duplicate = self.writer.prepare_commit(
            self._contract(order_id="ORDER_WRITER_2", candidate_lifecycle_event="PARTIAL_FILL"),
            self._plan(),
        )

        self.assertFalse(duplicate["ok"])
        self.assertIn("duplicate lifecycle transition exists", duplicate["issues"])

    def test_order_id_and_event_duplicate_is_blocked_for_prepared_transition(self) -> None:
        first = self.writer.prepare_commit(self._contract(evidence_id="EVIDENCE_A"), self._plan())

        second = self.writer.prepare_commit(self._contract(evidence_id="EVIDENCE_B"), self._plan())

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn("duplicate lifecycle transition exists", second["issues"])

    def test_writer_prepare_commit_creates_journal_record(self) -> None:
        prepared = self.writer.prepare_commit(self._contract(), self._plan())

        self.assertTrue(prepared["ok"])
        journal = _rows(self.db_path, "journal")
        self.assertEqual(1, len(journal))
        self.assertEqual(prepared["commit_token"], journal[0]["commit_token"])
        self.assertEqual("prepared", journal[0]["action"])

    def test_service_commits_when_external_executors_succeed(self) -> None:
        runtime_calls: list[dict[str, object]] = []
        queue_calls: list[dict[str, object]] = []

        def runtime_executor(plan: dict[str, object]) -> dict[str, object]:
            runtime_calls.append(plan)
            return {"ok": True}

        def queue_executor(plan: dict[str, object]) -> dict[str, object]:
            queue_calls.append(plan)
            return {"ok": True}

        result = commit_lifecycle(
            self._preview(),
            self._orchestrator_result(),
            self.writer,
            runtime_commit_executor=runtime_executor,
            queue_commit_executor=queue_executor,
        )

        self.assertEqual("COMMITTED", result["status"])
        self.assertTrue(result["commit_token"])
        self.assertEqual(1, len(runtime_calls))
        self.assertEqual(1, len(queue_calls))
        self.assertEqual("committed", _rows(self.db_path, "transitions")[0]["status"])

    def test_service_aborts_when_external_executor_fails_and_keeps_prepare_and_journal(self) -> None:
        def runtime_executor(plan: dict[str, object]) -> dict[str, object]:
            return {"ok": False, "issues": ["runtime failed"]}

        result = commit_lifecycle(
            self._preview(),
            self._orchestrator_result(),
            self.writer,
            runtime_commit_executor=runtime_executor,
            queue_commit_executor=lambda plan: {"ok": True},
        )

        self.assertEqual("ABORTED", result["status"])
        self.assertIn("runtime failed", result["issues"])
        transitions = _rows(self.db_path, "transitions")
        journal_actions = [row["action"] for row in _rows(self.db_path, "journal")]
        self.assertEqual(1, len(transitions))
        self.assertEqual("aborted", transitions[0]["status"])
        self.assertIn("prepared", journal_actions)
        self.assertIn("aborted", journal_actions)

    def test_service_executor_failure_leaves_auditable_transition_record(self) -> None:
        result = commit_lifecycle(
            self._preview(),
            self._orchestrator_result(),
            self.writer,
            runtime_commit_executor=lambda plan: {"ok": True},
            queue_commit_executor=lambda plan: {"ok": False, "issues": ["queue failed"]},
        )

        self.assertEqual("ABORTED", result["status"])
        transitions = _rows(self.db_path, "transitions")
        journal = _rows(self.db_path, "journal")
        self.assertEqual(1, len(transitions))
        self.assertEqual("aborted", transitions[0]["status"])
        self.assertEqual("ORDER_WRITER_1", transitions[0]["order_id"])
        self.assertEqual(["prepared", "aborted"], [row["action"] for row in journal])

    def test_read_store_snapshot_returns_transitions(self) -> None:
        prepared = self.writer.prepare_commit(self._contract(), self._plan())
        self.writer.finalize_commit(prepared["commit_token"], success=True)

        snapshot = self.writer.read_store_snapshot("temp_store")

        self.assertTrue(snapshot["snapshot_valid"])
        self.assertEqual("temp_store", snapshot["lifecycle_store"])
        self.assertEqual(1, len(snapshot["existing_transitions"]))
        self.assertEqual("ORDER_WRITER_1", snapshot["existing_transitions"][0]["order_id"])
        self.assertEqual("committed", snapshot["existing_transitions"][0]["status"])

    def test_prepare_commit_rolls_back_transaction_on_failure(self) -> None:
        def boom(conn: sqlite3.Connection, order_id: str, event: str, evidence_id: str) -> None:
            raise RuntimeError("forced duplicate check failure")

        self.writer._existing_duplicate = boom  # type: ignore[method-assign]

        result = self.writer.prepare_commit(self._contract(), self._plan())

        self.assertFalse(result["ok"])
        self.assertIn("prepare failed: forced duplicate check failure", result["issues"])
        self.assertEqual([], _rows(self.db_path, "transitions"))
        self.assertEqual([], _rows(self.db_path, "journal"))

    def test_service_rejects_invalid_commit_plan_orchestrator(self) -> None:
        result = commit_lifecycle(
            self._preview(),
            self._orchestrator_result(orchestrator_type="WRONG_TYPE"),
            self.writer,
            runtime_commit_executor=lambda plan: {"ok": True},
            queue_commit_executor=lambda plan: {"ok": True},
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("INVALID_COMMIT_PLAN_ORCHESTRATOR_TYPE", result["issues"])
        self.assertEqual([], _rows(self.db_path, "transitions"))
        self.assertEqual([], _rows(self.db_path, "journal"))


if __name__ == "__main__":
    unittest.main()
