from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_journal import JOURNAL_TYPE, ExecutionJournal


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionJournalTest(unittest.TestCase):
    def test_append_event(self) -> None:
        journal = ExecutionJournal()

        event = journal.append_event("SESSION_CREATED", "SESSION_1", {"status": "CREATED"})

        self.assertEqual("EXEC_EVENT_000001", event["event_id"])
        self.assertEqual("SESSION_CREATED", event["event_type"])
        self.assertEqual("SESSION_1", event["session_id"])
        self.assertEqual({"status": "CREATED"}, event["payload"])
        self.assertEqual(1, len(journal.list_events()))

    def test_list_events(self) -> None:
        journal = ExecutionJournal()
        journal.append_event("SESSION_CREATED", "SESSION_1")
        journal.append_event("SESSION_READY", "SESSION_1")

        result = journal.list_events()

        self.assertEqual(["SESSION_CREATED", "SESSION_READY"], [event["event_type"] for event in result])

    def test_list_by_session(self) -> None:
        journal = ExecutionJournal()
        journal.append_event("SESSION_CREATED", "SESSION_1")
        journal.append_event("SESSION_CREATED", "SESSION_2")
        journal.append_event("SESSION_READY", "SESSION_1")

        result = journal.list_by_session("SESSION_1")

        self.assertEqual(["SESSION_CREATED", "SESSION_READY"], [event["event_type"] for event in result])
        with self.assertRaises(ValueError):
            journal.list_by_session("")

    def test_list_by_type(self) -> None:
        journal = ExecutionJournal()
        journal.append_event("SESSION_CREATED", "SESSION_1")
        journal.append_event("SESSION_READY", "SESSION_1")
        journal.append_event("SESSION_READY", "SESSION_2")

        result = journal.list_by_type("SESSION_READY")

        self.assertEqual(["SESSION_1", "SESSION_2"], [event["session_id"] for event in result])
        with self.assertRaises(ValueError):
            journal.list_by_type("")

    def test_latest_event(self) -> None:
        journal = ExecutionJournal()
        self.assertIsNone(journal.latest_event())
        journal.append_event("SESSION_CREATED", "SESSION_1")
        journal.append_event("SESSION_CREATED", "SESSION_2")
        journal.append_event("SESSION_READY", "SESSION_1")

        self.assertEqual("SESSION_READY", journal.latest_event()["event_type"])
        self.assertEqual("SESSION_READY", journal.latest_event("SESSION_1")["event_type"])
        self.assertEqual("SESSION_CREATED", journal.latest_event("SESSION_2")["event_type"])
        self.assertIsNone(journal.latest_event("SESSION_3"))

    def test_to_dict_from_dict(self) -> None:
        journal = ExecutionJournal()
        journal.append_event("SESSION_CREATED", "SESSION_1", {"status": "CREATED"})

        data = journal.to_dict()
        restored = ExecutionJournal.from_dict(data)

        self.assertEqual(JOURNAL_TYPE, data["journal_type"])
        self.assertTrue(data["in_memory"])
        self.assertFalse(data["runtime_write"])
        self.assertEqual(1, data["event_count"])
        self.assertEqual(journal.list_events(), restored.list_events())

    def test_copy_deepcopy(self) -> None:
        journal = ExecutionJournal()
        journal.append_event("SESSION_CREATED", "SESSION_1", {"nested": {"value": "original"}})

        copied = journal.copy()
        copied_event = copied.list_events()[0]
        copied_event["payload"]["nested"]["value"] = "mutated-copy-result"
        copied_from_dict = copied.to_dict()
        copied_from_dict["events"][0]["payload"]["nested"]["value"] = "mutated-copy-dict"

        self.assertEqual("original", journal.list_events()[0]["payload"]["nested"]["value"])
        self.assertEqual("original", copied.list_events()[0]["payload"]["nested"]["value"])

    def test_summary(self) -> None:
        journal = ExecutionJournal()
        journal.append_event("SESSION_CREATED", "SESSION_1")
        journal.append_event("SESSION_READY", "SESSION_1")
        journal.append_event("SESSION_CREATED", "SESSION_2")

        result = journal.summary()

        self.assertEqual("EXECUTION_JOURNAL_SUMMARY", result["journal_type"])
        self.assertEqual(3, result["event_count"])
        self.assertEqual(2, result["by_type"]["SESSION_CREATED"])
        self.assertEqual(2, result["by_session"]["SESSION_1"])
        self.assertEqual("SESSION_CREATED", result["latest_event"]["event_type"])

    def test_event_id_uniqueness(self) -> None:
        journal = ExecutionJournal()
        first = journal.append_event("SESSION_CREATED", "SESSION_1")
        second = journal.append_event("SESSION_READY", "SESSION_1")

        self.assertNotEqual(first["event_id"], second["event_id"])
        with self.assertRaises(ValueError):
            ExecutionJournal.from_dict(
                {
                    "events": [
                        first,
                        {
                            **second,
                            "event_id": first["event_id"],
                        },
                    ]
                }
            )

    def test_payload_deepcopy(self) -> None:
        journal = ExecutionJournal()
        payload = {"nested": {"value": "original"}}

        event = journal.append_event("SESSION_CREATED", "SESSION_1", payload)
        payload["nested"]["value"] = "mutated-input"
        event["payload"]["nested"]["value"] = "mutated-return"

        self.assertEqual("original", journal.list_events()[0]["payload"]["nested"]["value"])

    def test_external_mutation_blocked(self) -> None:
        journal = ExecutionJournal()
        journal.append_event("SESSION_CREATED", "SESSION_1", {"nested": {"value": "original"}})

        events = journal.list_events()
        events[0]["payload"]["nested"]["value"] = "mutated-list"
        latest = journal.latest_event()
        latest["payload"]["nested"]["value"] = "mutated-latest"
        data = journal.to_dict()
        data["events"][0]["payload"]["nested"]["value"] = "mutated-dict"
        summary = journal.summary()
        summary["latest_event"]["payload"]["nested"]["value"] = "mutated-summary"

        self.assertEqual("original", journal.list_events()[0]["payload"]["nested"]["value"])

    def test_invalid_event_blocked(self) -> None:
        journal = ExecutionJournal()

        with self.assertRaises(ValueError):
            journal.append_event("", "SESSION_1")
        with self.assertRaises(ValueError):
            journal.append_event("SESSION_CREATED", "")
        with self.assertRaises(ValueError):
            journal.append_event("SESSION_CREATED", "SESSION_1", payload="bad")
        with self.assertRaises(ValueError):
            ExecutionJournal.from_dict("bad")
        with self.assertRaises(ValueError):
            ExecutionJournal.from_dict({"events": "bad"})
        with self.assertRaises(ValueError):
            ExecutionJournal.from_dict({"events": [{"event_id": "E1"}]})
        with self.assertRaises(ValueError):
            ExecutionJournal.from_dict(
                {
                    "events": [
                        {
                            "event_id": "E1",
                            "event_type": "SESSION_CREATED",
                            "session_id": "SESSION_1",
                            "created_at": "2026-07-06T00:00:00+00:00",
                            "payload": "bad",
                        }
                    ]
                }
            )

    def test_no_runtime_storage_send_order_queue_gui_connections(self) -> None:
        import execution_journal

        module_text = execution_journal.__loader__.get_source(execution_journal.__name__)

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
            journal = ExecutionJournal()
            journal.append_event("SESSION_CREATED", "SESSION_1")
            journal.to_dict()
            journal.copy()
            journal.summary()

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

        journal = ExecutionJournal()
        journal.append_event("SESSION_CREATED", "SESSION_1")
        journal.append_event("SESSION_READY", "SESSION_1")
        journal.to_dict()
        journal.copy()
        journal.summary()

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
