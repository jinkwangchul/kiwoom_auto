from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_context import CONTEXT_TYPE, ExecutionContext
from execution_journal import ExecutionJournal
from execution_session import STATUS_BLOCKED, STATUS_COMPLETED, STATUS_INVALID, STATUS_READY, create_session
from execution_state import ExecutionState


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionContextTest(unittest.TestCase):
    def _context(self) -> ExecutionContext:
        return ExecutionContext(metadata={"source": {"name": "unit-test"}})

    def _create_session(self, context: ExecutionContext | None = None, suffix: str = "1"):
        ctx = context or self._context()
        return ctx.create_session(
            session_id=f"SESSION_{suffix}",
            created_at="2026-07-06T00:00:00+00:00",
            execution_id=f"EXEC_{suffix}",
            order_id=f"ORDER_{suffix}",
            request_hash=f"HASH_{suffix}",
            lock_id=f"LOCK_{suffix}",
            metadata={"nested": {"value": suffix}},
        )

    def test_create_session_creates_state_and_journal_event(self) -> None:
        context = self._context()

        session = self._create_session(context)

        self.assertEqual("SESSION_1", session.session_id)
        self.assertIsNotNone(context.get_session("SESSION_1"))
        events = context.list_events("SESSION_1")
        self.assertEqual(["SESSION_CREATED"], [event["event_type"] for event in events])
        self.assertEqual("EXEC_1", events[0]["payload"]["execution_id"])

    def test_mark_ready_records_event(self) -> None:
        context = self._context()
        self._create_session(context)

        session = context.mark_session_ready("SESSION_1")

        self.assertEqual(STATUS_READY, session.status)
        self.assertEqual(STATUS_READY, context.get_session("SESSION_1").status)
        self.assertEqual("SESSION_READY", context.list_events("SESSION_1")[-1]["event_type"])

    def test_mark_blocked_records_event(self) -> None:
        context = self._context()
        self._create_session(context)

        session = context.mark_session_blocked("SESSION_1", "WAITING")

        self.assertEqual(STATUS_BLOCKED, session.status)
        self.assertEqual("WAITING", session.reason)
        event = context.list_events("SESSION_1")[-1]
        self.assertEqual("SESSION_BLOCKED", event["event_type"])
        self.assertEqual("WAITING", event["payload"]["reason"])

    def test_mark_invalid_records_event(self) -> None:
        context = self._context()
        self._create_session(context)

        session = context.mark_session_invalid("SESSION_1", "INVALID_INPUT")

        self.assertEqual(STATUS_INVALID, session.status)
        event = context.list_events("SESSION_1")[-1]
        self.assertEqual("SESSION_INVALID", event["event_type"])
        self.assertEqual("INVALID_INPUT", event["payload"]["reason"])

    def test_mark_completed_records_event(self) -> None:
        context = self._context()
        self._create_session(context)
        context.mark_session_ready("SESSION_1")

        session = context.mark_session_completed("SESSION_1")

        self.assertEqual(STATUS_COMPLETED, session.status)
        self.assertEqual("SESSION_COMPLETED", context.list_events("SESSION_1")[-1]["event_type"])

    def test_invalid_transition_does_not_record_success_event(self) -> None:
        context = self._context()
        self._create_session(context)
        before = context.list_events("SESSION_1")

        with self.assertRaises(ValueError):
            context.mark_session_completed("SESSION_1")

        self.assertEqual(before, context.list_events("SESSION_1"))
        self.assertNotIn("SESSION_COMPLETED", [event["event_type"] for event in context.list_events("SESSION_1")])

    def test_get_list(self) -> None:
        context = self._context()
        self._create_session(context, "1")
        self._create_session(context, "2")

        self.assertEqual("SESSION_1", context.get_session("SESSION_1").session_id)
        self.assertEqual(["SESSION_1", "SESSION_2"], [session.session_id for session in context.list_sessions()])
        self.assertIsNone(context.get_session("UNKNOWN"))

    def test_list_events_by_session(self) -> None:
        context = self._context()
        self._create_session(context, "1")
        self._create_session(context, "2")
        context.mark_session_ready("SESSION_1")

        self.assertEqual(3, len(context.list_events()))
        self.assertEqual(
            ["SESSION_CREATED", "SESSION_READY"],
            [event["event_type"] for event in context.list_events("SESSION_1")],
        )

    def test_to_dict_from_dict(self) -> None:
        context = self._context()
        self._create_session(context)
        context.mark_session_ready("SESSION_1")

        data = context.to_dict()
        restored = ExecutionContext.from_dict(data)

        self.assertEqual(CONTEXT_TYPE, data["context_type"])
        self.assertTrue(data["in_memory"])
        self.assertFalse(data["runtime_write"])
        self.assertEqual(STATUS_READY, restored.get_session("SESSION_1").status)
        self.assertEqual(context.list_events(), restored.list_events())

    def test_copy_deepcopy(self) -> None:
        context = self._context()
        self._create_session(context)
        copied = context.copy()

        copied.metadata["source"]["name"] = "mutated-copy"
        copied_session = copied.get_session("SESSION_1")
        copied_session.metadata["nested"]["value"] = "mutated-session"
        copied.state.update_session(copied_session)

        self.assertEqual("unit-test", context.metadata["source"]["name"])
        self.assertEqual("1", context.get_session("SESSION_1").metadata["nested"]["value"])
        self.assertEqual("mutated-session", copied.get_session("SESSION_1").metadata["nested"]["value"])

    def test_summary(self) -> None:
        context = self._context()
        self._create_session(context)
        context.mark_session_ready("SESSION_1")

        result = context.summary()

        self.assertEqual("EXECUTION_CONTEXT_SUMMARY", result["context_type"])
        self.assertEqual(1, result["state"]["session_count"])
        self.assertEqual(2, result["journal"]["event_count"])
        self.assertEqual("unit-test", result["metadata"]["source"]["name"])

    def test_external_mutation_blocked(self) -> None:
        context = self._context()
        session = self._create_session(context)
        session.metadata["nested"]["value"] = "mutated-return"
        fetched = context.get_session("SESSION_1")
        fetched.metadata["nested"]["value"] = "mutated-fetch"
        data = context.to_dict()
        data["metadata"]["source"]["name"] = "mutated-dict"
        data["state"]["sessions"][0]["metadata"]["nested"]["value"] = "mutated-dict"
        data["journal"]["events"][0]["payload"]["status"] = "MUTATED"
        summary = context.summary()
        summary["metadata"]["source"]["name"] = "mutated-summary"

        self.assertEqual("unit-test", context.metadata["source"]["name"])
        self.assertEqual("1", context.get_session("SESSION_1").metadata["nested"]["value"])
        self.assertEqual("CREATED", context.list_events("SESSION_1")[0]["payload"]["status"])

    def test_invalid_context_input(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionContext(state="bad")
        with self.assertRaises(ValueError):
            ExecutionContext(journal="bad")
        with self.assertRaises(ValueError):
            ExecutionContext(metadata="bad")
        with self.assertRaises(ValueError):
            ExecutionContext.from_dict("bad")
        with self.assertRaises(ValueError):
            ExecutionContext.from_dict({"state": {}, "journal": {"events": []}})
        with self.assertRaises(ValueError):
            self._context().mark_session_ready("UNKNOWN")

    def test_constructor_copies_state_journal_metadata(self) -> None:
        session = create_session(
            session_id="SESSION_1",
            created_at="2026-07-06T00:00:00+00:00",
            execution_id="EXEC_1",
            order_id="ORDER_1",
            request_hash="HASH_1",
            lock_id="LOCK_1",
            metadata={"nested": {"value": "1"}},
        )
        state = ExecutionState([session])
        journal = ExecutionJournal()
        journal.append_event("SESSION_CREATED", "SESSION_1")
        metadata = {"source": {"name": "source"}}

        context = ExecutionContext(state=state, journal=journal, metadata=metadata)
        state.get_session("SESSION_1").metadata["nested"]["value"] = "mutated-state-copy"
        journal_event = journal.list_events()[0]
        journal_event["payload"]["mutated"] = True
        metadata["source"]["name"] = "mutated-metadata"

        self.assertEqual("1", context.get_session("SESSION_1").metadata["nested"]["value"])
        self.assertNotIn("mutated", context.list_events()[0]["payload"])
        self.assertEqual("source", context.metadata["source"]["name"])

    def test_no_runtime_storage_send_order_queue_gui_connections(self) -> None:
        import execution_context

        module_text = execution_context.__loader__.get_source(execution_context.__name__)

        self.assertNotIn("read_order_", module_text)
        self.assertNotIn("ExecutionRuntimeStorage", module_text)
        self.assertNotIn("commit_execution_runtime", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text.lower())
        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)

    def test_no_file_write(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            context = self._context()
            self._create_session(context)
            context.mark_session_ready("SESSION_1")
            context.to_dict()
            context.copy()
            context.summary()

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

        context = self._context()
        self._create_session(context)
        context.mark_session_ready("SESSION_1")
        context.to_dict()
        context.copy()
        context.summary()

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
