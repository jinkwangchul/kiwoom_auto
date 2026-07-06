from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_context import ExecutionContext
from execution_recovery import SNAPSHOT_TYPE, ExecutionRecovery


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRecoveryTest(unittest.TestCase):
    def _context(self) -> ExecutionContext:
        context = ExecutionContext(metadata={"source": {"name": "unit-test"}})
        context.create_session(
            session_id="SESSION_1",
            created_at="2026-07-06T00:00:00+00:00",
            execution_id="EXEC_1",
            order_id="ORDER_1",
            request_hash="HASH_1",
            lock_id="LOCK_1",
            metadata={"nested": {"value": "1"}},
        )
        context.mark_session_ready("SESSION_1")
        return context

    def test_create_snapshot(self) -> None:
        snapshot = ExecutionRecovery().create_snapshot(self._context())

        self.assertEqual(SNAPSHOT_TYPE, snapshot["snapshot_type"])
        self.assertTrue(snapshot["created_at"])
        self.assertEqual("EXECUTION_CONTEXT", snapshot["context"]["context_type"])
        self.assertEqual(1, snapshot["context"]["state"]["session_count"])
        self.assertEqual(2, snapshot["context"]["journal"]["event_count"])

    def test_restore_snapshot(self) -> None:
        recovery = ExecutionRecovery()
        snapshot = recovery.create_snapshot(self._context())

        restored = recovery.restore_snapshot(snapshot)

        self.assertIsInstance(restored, ExecutionContext)
        self.assertEqual("READY", restored.get_session("SESSION_1").status)
        self.assertEqual(["SESSION_CREATED", "SESSION_READY"], [event["event_type"] for event in restored.list_events("SESSION_1")])

    def test_validate_valid_snapshot(self) -> None:
        snapshot = ExecutionRecovery().create_snapshot(self._context())

        result = ExecutionRecovery().validate_snapshot(snapshot)

        self.assertTrue(result["valid"])
        self.assertEqual([], result["issues"])

    def test_validate_malformed_snapshot(self) -> None:
        recovery = ExecutionRecovery()

        self.assertFalse(recovery.validate_snapshot("bad")["valid"])
        self.assertIn("INVALID_SNAPSHOT_TYPE", recovery.validate_snapshot({"context": {}})["issues"])
        self.assertIn("MISSING_CONTEXT", recovery.validate_snapshot({"snapshot_type": SNAPSHOT_TYPE})["issues"])

        snapshot = recovery.create_snapshot(self._context())
        snapshot["context"].pop("state")
        result = recovery.validate_snapshot(snapshot)
        self.assertFalse(result["valid"])
        self.assertIn("MISSING_STATE", result["issues"])

    def test_snapshot_deepcopy(self) -> None:
        context = self._context()
        snapshot = ExecutionRecovery().create_snapshot(context)
        snapshot["context"]["metadata"]["source"]["name"] = "mutated-snapshot"
        snapshot["context"]["state"]["sessions"][0]["metadata"]["nested"]["value"] = "mutated-snapshot"

        self.assertEqual("unit-test", context.metadata["source"]["name"])
        self.assertEqual("1", context.get_session("SESSION_1").metadata["nested"]["value"])

    def test_restored_context_deepcopy(self) -> None:
        recovery = ExecutionRecovery()
        snapshot = recovery.create_snapshot(self._context())
        restored = recovery.restore_snapshot(snapshot)
        snapshot["context"]["metadata"]["source"]["name"] = "mutated-after-restore"
        snapshot["context"]["state"]["sessions"][0]["metadata"]["nested"]["value"] = "mutated-after-restore"

        self.assertEqual("unit-test", restored.metadata["source"]["name"])
        self.assertEqual("1", restored.get_session("SESSION_1").metadata["nested"]["value"])

    def test_restored_state_same_content(self) -> None:
        recovery = ExecutionRecovery()
        snapshot = recovery.create_snapshot(self._context())
        restored = recovery.restore_snapshot(snapshot)

        self.assertEqual(snapshot["context"]["state"], restored.state.to_dict())

    def test_restored_journal_same_content(self) -> None:
        recovery = ExecutionRecovery()
        snapshot = recovery.create_snapshot(self._context())
        restored = recovery.restore_snapshot(snapshot)

        self.assertEqual(snapshot["context"]["journal"], restored.journal.to_dict())

    def test_restored_metadata_same_content(self) -> None:
        recovery = ExecutionRecovery()
        snapshot = recovery.create_snapshot(self._context())
        restored = recovery.restore_snapshot(snapshot)

        self.assertEqual(snapshot["context"]["metadata"], restored.metadata)

    def test_external_mutation_blocked(self) -> None:
        recovery = ExecutionRecovery()
        snapshot = recovery.create_snapshot(self._context())
        copied = recovery.copy_snapshot(snapshot)
        copied["context"]["metadata"]["source"]["name"] = "mutated-copy"
        summary = recovery.summary(snapshot)
        summary["issues"].append("MUTATED_SUMMARY")
        restored = recovery.restore_snapshot(snapshot)
        restored.metadata["source"]["name"] = "mutated-restored"

        self.assertEqual("unit-test", snapshot["context"]["metadata"]["source"]["name"])
        self.assertEqual([], recovery.validate_snapshot(snapshot)["issues"])

    def test_summary(self) -> None:
        snapshot = ExecutionRecovery().create_snapshot(self._context())

        result = ExecutionRecovery().summary(snapshot)

        self.assertEqual(SNAPSHOT_TYPE, result["snapshot_type"])
        self.assertTrue(result["valid"])
        self.assertEqual(1, result["session_count"])
        self.assertEqual(2, result["event_count"])
        self.assertEqual([], result["issues"])

    def test_restore_invalid_snapshot_raises(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionRecovery().restore_snapshot({"snapshot_type": SNAPSHOT_TYPE})

    def test_invalid_session_and_event_structures(self) -> None:
        recovery = ExecutionRecovery()
        snapshot = recovery.create_snapshot(self._context())
        snapshot["context"]["state"]["sessions"][0]["session_id"] = ""
        snapshot["context"]["journal"]["events"][0]["payload"] = "bad"
        snapshot["context"]["journal"]["events"][1]["event_id"] = snapshot["context"]["journal"]["events"][0]["event_id"]

        result = recovery.validate_snapshot(snapshot)

        self.assertFalse(result["valid"])
        self.assertIn("SESSION_0_MISSING_SESSION_ID", result["issues"])
        self.assertIn("EVENT_0_PAYLOAD_MUST_BE_DICT", result["issues"])
        self.assertIn("EVENT_1_DUPLICATE_EVENT_ID", result["issues"])

    def test_no_runtime_storage_send_order_queue_gui_connections(self) -> None:
        import execution_recovery

        module_text = execution_recovery.__loader__.get_source(execution_recovery.__name__)

        self.assertNotIn("read_order_", module_text)
        self.assertNotIn("ExecutionRuntimeStorage", module_text)
        self.assertNotIn("commit_execution_runtime", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text.lower())
        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)

    def test_no_file_io(self) -> None:
        recovery = ExecutionRecovery()
        context = self._context()
        with (
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            snapshot = recovery.create_snapshot(context)
            recovery.validate_snapshot(snapshot)
            recovery.restore_snapshot(snapshot)
            recovery.copy_snapshot(snapshot)
            recovery.summary(snapshot)

        read_text.assert_not_called()
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        recovery = ExecutionRecovery()
        snapshot = recovery.create_snapshot(self._context())
        recovery.validate_snapshot(snapshot)
        recovery.restore_snapshot(snapshot)
        recovery.copy_snapshot(snapshot)
        recovery.summary(snapshot)

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
