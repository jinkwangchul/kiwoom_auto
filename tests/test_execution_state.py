from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_session import STATUS_BLOCKED, STATUS_COMPLETED, STATUS_CREATED, STATUS_READY, create_session
from execution_state import ExecutionState


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionStateTest(unittest.TestCase):
    def _session(self, suffix: str = "1"):
        return create_session(
            session_id=f"SESSION_{suffix}",
            created_at="2026-07-06T00:00:00+00:00",
            execution_id=f"EXEC_{suffix}",
            order_id=f"ORDER_{suffix}",
            request_hash=f"HASH_{suffix}",
            lock_id=f"LOCK_{suffix}",
            metadata={"nested": {"value": suffix}},
        )

    def test_add_get(self) -> None:
        state = ExecutionState()
        session = self._session()

        state.add_session(session)
        result = state.get_session("SESSION_1")

        self.assertIsNotNone(result)
        self.assertEqual("SESSION_1", result.session_id)
        self.assertEqual(STATUS_CREATED, result.status)

    def test_duplicate_add_blocked(self) -> None:
        state = ExecutionState()
        state.add_session(self._session())

        with self.assertRaises(ValueError):
            state.add_session(self._session())

    def test_update(self) -> None:
        state = ExecutionState()
        session = self._session()
        state.add_session(session)
        updated = session.copy()
        updated.mark_ready()

        state.update_session(updated)

        self.assertEqual(STATUS_READY, state.get_session("SESSION_1").status)
        with self.assertRaises(ValueError):
            state.update_session(self._session("UNKNOWN"))

    def test_remove(self) -> None:
        state = ExecutionState([self._session()])

        removed = state.remove_session("SESSION_1")

        self.assertIsNotNone(removed)
        self.assertEqual("SESSION_1", removed.session_id)
        self.assertFalse(state.has_session("SESSION_1"))
        self.assertIsNone(state.remove_session("SESSION_1"))

    def test_list_sessions(self) -> None:
        state = ExecutionState([self._session("1"), self._session("2")])

        result = state.list_sessions()

        self.assertEqual(["SESSION_1", "SESSION_2"], [session.session_id for session in result])

    def test_list_by_status(self) -> None:
        ready = self._session("1")
        ready.mark_ready()
        blocked = self._session("2")
        blocked.mark_blocked("WAITING")
        state = ExecutionState([ready, blocked, self._session("3")])

        self.assertEqual(["SESSION_1"], [session.session_id for session in state.list_by_status(STATUS_READY)])
        self.assertEqual(["SESSION_2"], [session.session_id for session in state.list_by_status(STATUS_BLOCKED)])
        self.assertEqual(["SESSION_3"], [session.session_id for session in state.list_by_status(STATUS_CREATED)])
        with self.assertRaises(ValueError):
            state.list_by_status("UNKNOWN")

    def test_has_session(self) -> None:
        state = ExecutionState([self._session()])

        self.assertTrue(state.has_session("SESSION_1"))
        self.assertFalse(state.has_session("SESSION_2"))

    def test_to_dict_from_dict(self) -> None:
        ready = self._session()
        ready.mark_ready()
        state = ExecutionState([ready])

        data = state.to_dict()
        restored = ExecutionState.from_dict(data)

        self.assertEqual("EXECUTION_STATE", data["state_type"])
        self.assertTrue(data["in_memory"])
        self.assertFalse(data["runtime_write"])
        self.assertEqual(1, data["session_count"])
        self.assertEqual(STATUS_READY, restored.get_session("SESSION_1").status)

    def test_copy_deepcopy(self) -> None:
        state = ExecutionState([self._session()])
        copied = state.copy()
        copied_session = copied.get_session("SESSION_1")
        copied_session.metadata["nested"]["value"] = "mutated"
        copied.update_session(copied_session)

        self.assertEqual("1", state.get_session("SESSION_1").metadata["nested"]["value"])
        self.assertEqual("mutated", copied.get_session("SESSION_1").metadata["nested"]["value"])

    def test_summary(self) -> None:
        ready = self._session("1")
        ready.mark_ready()
        completed = self._session("2")
        completed.mark_ready().mark_completed()
        state = ExecutionState([ready, completed, self._session("3")])

        result = state.summary()

        self.assertEqual("EXECUTION_STATE_SUMMARY", result["state_type"])
        self.assertEqual(3, result["session_count"])
        self.assertEqual(1, result["by_status"][STATUS_READY])
        self.assertEqual(1, result["by_status"][STATUS_COMPLETED])
        self.assertEqual(1, result["by_status"][STATUS_CREATED])
        self.assertEqual(["SESSION_1", "SESSION_2", "SESSION_3"], result["session_ids"])

    def test_external_mutation_blocked(self) -> None:
        state = ExecutionState()
        session = self._session()
        state.add_session(session)

        session.metadata["nested"]["value"] = "mutated-original"
        fetched = state.get_session("SESSION_1")
        fetched.metadata["nested"]["value"] = "mutated-fetched"
        listed = state.list_sessions()
        listed[0].metadata["nested"]["value"] = "mutated-listed"
        data = state.to_dict()
        data["sessions"][0]["metadata"]["nested"]["value"] = "mutated-dict"

        self.assertEqual("1", state.get_session("SESSION_1").metadata["nested"]["value"])

    def test_invalid_session_blocked(self) -> None:
        state = ExecutionState()

        with self.assertRaises(ValueError):
            state.add_session({"session_id": "BAD"})
        with self.assertRaises(ValueError):
            state.update_session({"session_id": "BAD"})
        with self.assertRaises(ValueError):
            ExecutionState.from_dict("bad")
        with self.assertRaises(ValueError):
            ExecutionState.from_dict({"sessions": "bad"})

    def test_no_runtime_storage_send_order_queue_gui_connections(self) -> None:
        import execution_state

        module_text = execution_state.__loader__.get_source(execution_state.__name__)

        self.assertNotIn("read_order_", module_text)
        self.assertNotIn("ExecutionRuntimeStorage", module_text)
        self.assertNotIn("commit_execution_runtime", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text.lower())
        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            state = ExecutionState([self._session()])
            state.to_dict()
            state.copy()
            state.summary()

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

        state = ExecutionState([self._session()])
        state.get_session("SESSION_1")
        state.list_sessions()
        state.to_dict()
        state.copy()
        state.summary()

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
