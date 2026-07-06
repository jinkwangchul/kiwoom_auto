from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_context import ExecutionContext
from execution_context_adapter import ADAPTER_TYPE, adapt_runtime_dry_run_to_context


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionContextAdapterTest(unittest.TestCase):
    def _dry_run(self, status: str = "READY", *, include_ids: bool = True) -> dict:
        catalog = {
            "status": "READY",
            "execution_id": "EXEC_1" if include_ids else None,
            "order_id": "ORDER_1" if include_ids else None,
            "request_hash": "HASH_1" if include_ids else None,
            "lock_id": "LOCK_1" if include_ids else None,
        }
        return {
            "controller_type": "EXECUTION_RUNTIME_DRY_RUN_CONTROLLER",
            "status": status,
            "dry_run": True,
            "preview_only": True,
            "runtime_write": False,
            "send_order_called": False,
            "queue_commit_called": False,
            "runtime_commit_called": False,
            "catalog_orchestrator": {
                "status": "READY",
                "catalog_preview": catalog,
                "issues": [],
                "warnings": ["Preview mode"],
            },
            "commit_plan": {
                "status": status,
                "commit_plan": {
                    "planned_records": {
                        "execution": {
                            "execution_id": "EXEC_1" if include_ids else None,
                            "order_id": "ORDER_1" if include_ids else None,
                            "request_hash": "HASH_1" if include_ids else None,
                            "lock_id": "LOCK_1" if include_ids else None,
                        },
                        "lock": {
                            "lock_id": "LOCK_1" if include_ids else None,
                            "order_id": "ORDER_1" if include_ids else None,
                            "request_hash": "HASH_1" if include_ids else None,
                            "execution_id": "EXEC_1" if include_ids else None,
                        },
                    }
                },
                "issues": [] if status == "READY" else [f"DRY_RUN_{status}"],
                "warnings": ["Preview mode"],
            },
            "issues": [] if status == "READY" else [f"DRY_RUN_{status}"],
            "warnings": ["Preview mode"],
        }

    def test_ready_dry_run_creates_ready_session_and_journal_event(self) -> None:
        context = ExecutionContext()

        result = adapt_runtime_dry_run_to_context(context, self._dry_run("READY"))

        self.assertEqual(ADAPTER_TYPE, result["adapter_type"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["context_write"])
        self.assertFalse(result["runtime_write"])
        session = context.get_session(result["session_id"])
        self.assertEqual("READY", session.status)
        self.assertEqual(["SESSION_CREATED", "SESSION_READY"], [event["event_type"] for event in context.list_events(session.session_id)])

    def test_blocked_dry_run_creates_blocked_session_and_journal_event(self) -> None:
        context = ExecutionContext()

        result = adapt_runtime_dry_run_to_context(context, self._dry_run("BLOCKED"))

        self.assertEqual("BLOCKED", result["status"])
        session = context.get_session(result["session_id"])
        self.assertEqual("BLOCKED", session.status)
        self.assertEqual("DRY_RUN_BLOCKED", session.reason)
        self.assertEqual("SESSION_BLOCKED", context.list_events(session.session_id)[-1]["event_type"])

    def test_invalid_dry_run_creates_invalid_session_and_journal_event(self) -> None:
        context = ExecutionContext()

        result = adapt_runtime_dry_run_to_context(context, self._dry_run("INVALID"))

        self.assertEqual("INVALID", result["status"])
        session = context.get_session(result["session_id"])
        self.assertEqual("INVALID", session.status)
        self.assertEqual("DRY_RUN_INVALID", session.reason)
        self.assertEqual("SESSION_INVALID", context.list_events(session.session_id)[-1]["event_type"])

    def test_malformed_dry_run_creates_invalid_session(self) -> None:
        context = ExecutionContext()

        result = adapt_runtime_dry_run_to_context(context, "bad")

        self.assertEqual("INVALID", result["status"])
        self.assertTrue(result["context_write"])
        self.assertEqual("SESSION_INVALID_DRY_RUN", result["session_id"])
        self.assertIn("MALFORMED_DRY_RUN_RESULT", result["issues"])
        self.assertIn("MISSING_EXECUTION_ID", result["issues"])
        self.assertEqual("INVALID", context.get_session("SESSION_INVALID_DRY_RUN").status)

    def test_extracts_identifiers(self) -> None:
        context = ExecutionContext()

        result = adapt_runtime_dry_run_to_context(context, self._dry_run("READY"))
        session = context.get_session(result["session_id"])

        self.assertEqual("SESSION_EXEC_1", result["session_id"])
        self.assertEqual("EXEC_1", session.execution_id)
        self.assertEqual("ORDER_1", session.order_id)
        self.assertEqual("HASH_1", session.request_hash)
        self.assertEqual("LOCK_1", session.lock_id)

    def test_missing_identifiers_force_invalid_session(self) -> None:
        context = ExecutionContext()

        result = adapt_runtime_dry_run_to_context(context, self._dry_run("READY", include_ids=False))

        self.assertEqual("INVALID", result["status"])
        self.assertTrue(result["context_write"])
        self.assertIn("MISSING_EXECUTION_ID", result["issues"])
        self.assertEqual("INVALID", context.get_session(result["session_id"]).status)

    def test_context_mutation_is_intended_in_memory_only(self) -> None:
        context = ExecutionContext(metadata={"origin": {"name": "test"}})

        result = adapt_runtime_dry_run_to_context(context, self._dry_run("READY"))
        result["session_summary"]["status"] = "MUTATED_RESULT_ONLY"
        events = context.list_events(result["session_id"])
        events[-1]["payload"]["status"] = "MUTATED_EVENT_COPY"

        self.assertEqual("READY", context.get_session(result["session_id"]).status)
        self.assertEqual("READY", context.list_events(result["session_id"])[-1]["payload"]["status"])
        self.assertEqual("test", context.metadata["origin"]["name"])

    def test_invalid_context_is_invalid_without_write(self) -> None:
        result = adapt_runtime_dry_run_to_context(object(), self._dry_run("READY"))

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["context_write"])
        self.assertIn("CONTEXT_MUST_BE_EXECUTION_CONTEXT", result["issues"])

    def test_duplicate_session_blocks_second_adaptation(self) -> None:
        context = ExecutionContext()
        first = adapt_runtime_dry_run_to_context(context, self._dry_run("READY"))
        second = adapt_runtime_dry_run_to_context(context, self._dry_run("READY"))

        self.assertEqual("READY", first["status"])
        self.assertEqual("INVALID", second["status"])
        self.assertFalse(second["context_write"])
        self.assertTrue(any("CONTEXT_ADAPTATION_FAILED" in issue for issue in second["issues"]))

    def test_no_runtime_storage_send_order_queue_gui_connections(self) -> None:
        import execution_context_adapter

        module_text = execution_context_adapter.__loader__.get_source(execution_context_adapter.__name__)

        self.assertNotIn("read_order_", module_text)
        self.assertNotIn("ExecutionRuntimeStorage", module_text)
        self.assertNotIn("commit_execution_runtime", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text.lower())
        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)

    def test_no_file_write(self) -> None:
        context = ExecutionContext()
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = adapt_runtime_dry_run_to_context(context, self._dry_run("READY"))

        self.assertEqual("READY", result["status"])
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

        context = ExecutionContext()
        adapt_runtime_dry_run_to_context(context, self._dry_run("READY"))

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
