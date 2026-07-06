from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_session import (
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_CREATED,
    STATUS_INVALID,
    STATUS_READY,
    ExecutionSession,
    create_session,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionSessionTest(unittest.TestCase):
    def _session(self, **overrides) -> ExecutionSession:
        data = {
            "session_id": "SESSION_1",
            "created_at": "2026-07-06T00:00:00+00:00",
            "execution_id": "EXEC_1",
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "metadata": {"source": {"name": "unit-test"}},
        }
        data.update(overrides)
        return create_session(**data)

    def test_create_session(self) -> None:
        session = self._session()

        self.assertEqual("SESSION_1", session.session_id)
        self.assertEqual("EXEC_1", session.execution_id)
        self.assertEqual("ORDER_1", session.order_id)
        self.assertEqual("HASH_1", session.request_hash)
        self.assertEqual("LOCK_1", session.lock_id)
        self.assertEqual(STATUS_CREATED, session.status)

    def test_to_dict(self) -> None:
        session = self._session()

        result = session.to_dict()

        self.assertEqual("SESSION_1", result["session_id"])
        self.assertEqual(STATUS_CREATED, result["status"])
        self.assertEqual({"source": {"name": "unit-test"}}, result["metadata"])

    def test_from_dict(self) -> None:
        source = self._session().to_dict()
        source["status"] = STATUS_BLOCKED
        source["reason"] = "POLICY_BLOCKED"

        session = ExecutionSession.from_dict(source)

        self.assertEqual(STATUS_BLOCKED, session.status)
        self.assertEqual("POLICY_BLOCKED", session.reason)
        self.assertEqual("ORDER_1", session.order_id)

    def test_deepcopy_independence(self) -> None:
        source = self._session()
        data = source.to_dict()
        data["metadata"]["source"]["name"] = "mutated-dict"

        self.assertEqual("unit-test", source.metadata["source"]["name"])

        copied = source.copy()
        copied.metadata["source"]["name"] = "mutated-copy"

        self.assertEqual("unit-test", source.metadata["source"]["name"])
        self.assertEqual("mutated-copy", copied.metadata["source"]["name"])

    def test_immutable_id(self) -> None:
        session = self._session()

        with self.assertRaises(AttributeError):
            session.session_id = "SESSION_2"
        with self.assertRaises(AttributeError):
            session.execution_id = "EXEC_2"
        with self.assertRaises(AttributeError):
            session.order_id = "ORDER_2"
        with self.assertRaises(AttributeError):
            session.request_hash = "HASH_2"
        with self.assertRaises(AttributeError):
            session.lock_id = "LOCK_2"

    def test_ready_transition(self) -> None:
        session = self._session()

        returned = session.mark_ready()

        self.assertIs(session, returned)
        self.assertEqual(STATUS_READY, session.status)
        self.assertIsNone(session.reason)

    def test_blocked_transition(self) -> None:
        created = self._session()
        created.mark_blocked("WAITING")
        self.assertEqual(STATUS_BLOCKED, created.status)
        self.assertEqual("WAITING", created.reason)

        ready = self._session()
        ready.mark_ready().mark_blocked("POLICY_BLOCKED")
        self.assertEqual(STATUS_BLOCKED, ready.status)
        self.assertEqual("POLICY_BLOCKED", ready.reason)

        ready_again = ready.mark_ready()
        self.assertEqual(STATUS_READY, ready_again.status)

    def test_invalid_transition(self) -> None:
        created = self._session()
        created.mark_invalid("INVALID_INPUT")
        self.assertEqual(STATUS_INVALID, created.status)
        self.assertEqual("INVALID_INPUT", created.reason)

        ready = self._session()
        ready.mark_ready().mark_invalid("INVALID_RUNTIME")
        self.assertEqual(STATUS_INVALID, ready.status)

    def test_completed_transition(self) -> None:
        session = self._session()
        session.mark_ready().mark_completed()

        self.assertEqual(STATUS_COMPLETED, session.status)
        self.assertIsNone(session.reason)

    def test_invalid_transitions_raise(self) -> None:
        with self.assertRaises(ValueError):
            self._session().mark_completed()

        invalid = self._session()
        invalid.mark_invalid("BAD")
        with self.assertRaises(ValueError):
            invalid.mark_ready()
        with self.assertRaises(ValueError):
            invalid.mark_blocked("NOPE")
        with self.assertRaises(ValueError):
            invalid.mark_completed()

        completed = self._session()
        completed.mark_ready().mark_completed()
        with self.assertRaises(ValueError):
            completed.mark_ready()
        with self.assertRaises(ValueError):
            completed.mark_blocked("NOPE")
        with self.assertRaises(ValueError):
            completed.mark_invalid("NOPE")

    def test_summary(self) -> None:
        session = self._session()
        session.mark_ready().mark_completed()

        result = session.summary()

        self.assertEqual("SESSION_1", result["session_id"])
        self.assertEqual(STATUS_COMPLETED, result["status"])
        self.assertEqual("EXEC_1", result["execution_id"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertTrue(result["terminal"])

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            self._session(session_id="")
        with self.assertRaises(ValueError):
            ExecutionSession.from_dict("bad")
        with self.assertRaises(ValueError):
            ExecutionSession(
                session_id="SESSION_1",
                created_at="2026-07-06T00:00:00+00:00",
                execution_id="EXEC_1",
                order_id="ORDER_1",
                request_hash="HASH_1",
                lock_id="LOCK_1",
                status="UNKNOWN",
            )
        with self.assertRaises(ValueError):
            self._session().mark_blocked("")
        with self.assertRaises(ValueError):
            self._session().mark_invalid("")

    def test_no_runtime_storage_send_order_queue_gui_connections(self) -> None:
        import execution_session

        module_text = execution_session.__loader__.get_source(execution_session.__name__)

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
            session = self._session()
            session.mark_ready()
            copied = session.copy()

        self.assertEqual(STATUS_READY, copied.status)
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

        session = self._session()
        session.mark_ready().mark_completed()
        session.to_dict()
        session.copy()
        session.summary()

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
