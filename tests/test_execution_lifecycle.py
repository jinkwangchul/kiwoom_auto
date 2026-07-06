from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_context import ExecutionContext
from execution_lifecycle import LIFECYCLE_TYPE, ExecutionLifecycle
from execution_recovery import ExecutionRecovery


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionLifecycleTest(unittest.TestCase):
    def _lifecycle(self) -> ExecutionLifecycle:
        return ExecutionLifecycle(metadata={"source": {"name": "unit-test"}})

    def _start(self, lifecycle: ExecutionLifecycle | None = None):
        target = lifecycle or self._lifecycle()
        return target.start_session(
            session_id="SESSION_1",
            created_at="2026-07-06T00:00:00+00:00",
            execution_id="EXEC_1",
            order_id="ORDER_1",
            request_hash="HASH_1",
            lock_id="LOCK_1",
            metadata={"nested": {"value": "1"}},
        )

    def test_start_session(self) -> None:
        lifecycle = self._lifecycle()

        session = self._start(lifecycle)

        self.assertEqual("SESSION_1", session["session_id"])
        self.assertIsNotNone(lifecycle.context.get_session("SESSION_1"))
        self.assertEqual(
            ["SESSION_CREATED", "LIFECYCLE_SESSION_STARTED"],
            [event["event_type"] for event in lifecycle.context.list_events("SESSION_1")],
        )

    def test_ready_session(self) -> None:
        lifecycle = self._lifecycle()
        self._start(lifecycle)

        result = lifecycle.ready_session("SESSION_1")

        self.assertEqual("READY", result["status"])
        self.assertEqual("SESSION_READY", lifecycle.context.list_events("SESSION_1")[-1]["event_type"])

    def test_block_session(self) -> None:
        lifecycle = self._lifecycle()
        self._start(lifecycle)

        result = lifecycle.block_session("SESSION_1", "WAITING")

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("WAITING", result["reason"])
        self.assertEqual("SESSION_BLOCKED", lifecycle.context.list_events("SESSION_1")[-1]["event_type"])

    def test_invalidate_session(self) -> None:
        lifecycle = self._lifecycle()
        self._start(lifecycle)

        result = lifecycle.invalidate_session("SESSION_1", "INVALID_INPUT")

        self.assertEqual("INVALID", result["status"])
        self.assertEqual("INVALID_INPUT", result["reason"])
        self.assertEqual("SESSION_INVALID", lifecycle.context.list_events("SESSION_1")[-1]["event_type"])

    def test_complete_session(self) -> None:
        lifecycle = self._lifecycle()
        self._start(lifecycle)
        lifecycle.ready_session("SESSION_1")

        result = lifecycle.complete_session("SESSION_1")

        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("SESSION_COMPLETED", lifecycle.context.list_events("SESSION_1")[-1]["event_type"])

    def test_invalid_transition_does_not_record_success_event(self) -> None:
        lifecycle = self._lifecycle()
        self._start(lifecycle)
        before = lifecycle.context.list_events("SESSION_1")

        with self.assertRaises(ValueError):
            lifecycle.complete_session("SESSION_1")

        self.assertEqual(before, lifecycle.context.list_events("SESSION_1"))
        self.assertNotIn("SESSION_COMPLETED", [event["event_type"] for event in lifecycle.context.list_events("SESSION_1")])

    def test_snapshot(self) -> None:
        lifecycle = self._lifecycle()
        self._start(lifecycle)
        lifecycle.ready_session("SESSION_1")

        snapshot = lifecycle.snapshot()

        self.assertEqual("EXECUTION_CONTEXT_SNAPSHOT", snapshot["snapshot_type"])
        self.assertEqual("READY", snapshot["context"]["state"]["sessions"][0]["status"])
        snapshot["context"]["state"]["sessions"][0]["status"] = "MUTATED_SNAPSHOT"
        self.assertEqual("READY", lifecycle.context.get_session("SESSION_1").status)

    def test_restore(self) -> None:
        lifecycle = self._lifecycle()
        self._start(lifecycle)
        lifecycle.ready_session("SESSION_1")
        snapshot = lifecycle.snapshot()

        restored_lifecycle = self._lifecycle()
        result = restored_lifecycle.restore(snapshot)

        self.assertEqual("READY", restored_lifecycle.context.get_session("SESSION_1").status)
        self.assertEqual(result, restored_lifecycle.context.to_dict())

    def test_restore_independence(self) -> None:
        lifecycle = self._lifecycle()
        self._start(lifecycle)
        snapshot = lifecycle.snapshot()
        restored_lifecycle = self._lifecycle()
        restored_lifecycle.restore(snapshot)
        snapshot["context"]["metadata"]["source"] = {"name": "mutated-snapshot"}
        restored_session = restored_lifecycle.context.get_session("SESSION_1")
        restored_session.metadata["nested"]["value"] = "mutated-fetch"

        self.assertEqual("unit-test", lifecycle.metadata["source"]["name"])
        self.assertEqual("1", restored_lifecycle.context.get_session("SESSION_1").metadata["nested"]["value"])

    def test_summary(self) -> None:
        lifecycle = self._lifecycle()
        self._start(lifecycle)
        lifecycle.ready_session("SESSION_1")

        result = lifecycle.summary()

        self.assertEqual(LIFECYCLE_TYPE, result["lifecycle_type"])
        self.assertTrue(result["in_memory"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(1, result["context"]["state"]["session_count"])
        self.assertEqual(3, result["context"]["journal"]["event_count"])

    def test_constructor_validation_and_copy(self) -> None:
        context = ExecutionContext(metadata={"context": {"name": "source"}})
        self._start(ExecutionLifecycle(context=context))
        lifecycle = ExecutionLifecycle(context=context, recovery=ExecutionRecovery(), metadata={"nested": {"value": "1"}})
        lifecycle.metadata["nested"]["value"] = "mutated-lifecycle"

        self.assertEqual(0, len(context.list_sessions()))
        self.assertEqual("mutated-lifecycle", lifecycle.metadata["nested"]["value"])

        with self.assertRaises(ValueError):
            ExecutionLifecycle(context="bad")
        with self.assertRaises(ValueError):
            ExecutionLifecycle(recovery="bad")
        with self.assertRaises(ValueError):
            ExecutionLifecycle(metadata="bad")

    def test_no_runtime_storage_send_order_queue_gui_connections(self) -> None:
        import execution_lifecycle

        module_text = execution_lifecycle.__loader__.get_source(execution_lifecycle.__name__)

        self.assertNotIn("read_order_", module_text)
        self.assertNotIn("ExecutionRuntimeStorage", module_text)
        self.assertNotIn("commit_execution_runtime", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text.lower())
        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)

    def test_no_file_io(self) -> None:
        with (
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            lifecycle = self._lifecycle()
            self._start(lifecycle)
            lifecycle.ready_session("SESSION_1")
            snapshot = lifecycle.snapshot()
            lifecycle.restore(snapshot)
            lifecycle.summary()

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

        lifecycle = self._lifecycle()
        self._start(lifecycle)
        lifecycle.ready_session("SESSION_1")
        snapshot = lifecycle.snapshot()
        lifecycle.restore(snapshot)
        lifecycle.summary()

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
