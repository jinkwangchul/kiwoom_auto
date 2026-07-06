from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_lifecycle import ExecutionLifecycle
from execution_runtime_file_schema import default_order_executions_data, default_order_locks_data
from execution_runtime_manager import ExecutionRuntimeManager
from execution_runtime_storage import ExecutionRuntimeStorage


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class ExecutionRuntimeManagerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.executions_path = root / "order_executions.json"
        self.locks_path = root / "order_locks.json"
        _write_json(self.executions_path, default_order_executions_data())
        _write_json(self.locks_path, default_order_locks_data())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _storage(self) -> ExecutionRuntimeStorage:
        return ExecutionRuntimeStorage(self.executions_path, self.locks_path)

    def _manager(self) -> ExecutionRuntimeManager:
        return ExecutionRuntimeManager(
            lifecycle=ExecutionLifecycle(),
            storage=self._storage(),
            metadata={"contract": {"name": "runtime-manager"}},
        )

    def _order(self) -> dict:
        return {
            "id": "ORDER_CONTRACT_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_CONTRACT_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
            "order_intent": {
                "side": "BUY",
                "hoga": "MARKET",
            },
        }

    def _guard(self) -> dict:
        return {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "account_no": "12345678",
        }

    def _confirmations(self) -> dict:
        return {
            "manual_execution_runtime_commit_confirmed": True,
            "manual_runtime_file_write_confirmed": True,
        }

    def test_ready_dry_run_reflects_ready_session(self) -> None:
        manager = self._manager()

        result = manager.run_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertEqual("READY", result["status"])
        self.assertEqual("READY", manager.list_sessions()[0].status)

    def test_blocked_dry_run_reflects_blocked_session(self) -> None:
        manager = self._manager()

        result = manager.run_dry_run(self._order(), self._guard(), confirmations={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("BLOCKED", manager.list_sessions()[0].status)

    def test_invalid_dry_run_reflects_invalid_session(self) -> None:
        manager = self._manager()
        with mock.patch(
            "execution_runtime_manager.run_execution_runtime_dry_run",
            return_value={
                "status": "INVALID",
                "dry_run": True,
                "preview_only": True,
                "runtime_write": False,
                "issues": ["FORCED_INVALID"],
                "warnings": [],
            },
        ):
            result = manager.run_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertEqual("INVALID", result["status"])
        self.assertEqual("INVALID", manager.list_sessions()[0].status)
        self.assertIn("FORCED_INVALID", result["issues"])

    def test_returns_dry_run_and_context_adapter_results(self) -> None:
        result = self._manager().run_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertIn("dry_run_result", result)
        self.assertIn("context_adapter_result", result)
        self.assertEqual("READY", result["dry_run_result"]["status"])
        self.assertTrue(result["context_adapter_result"]["context_write"])

    def test_session_created_and_journal_events_recorded(self) -> None:
        manager = self._manager()

        manager.run_dry_run(self._order(), self._guard(), self._confirmations())
        session_id = manager.list_sessions()[0].session_id

        self.assertEqual(["SESSION_CREATED", "SESSION_READY"], [event["event_type"] for event in manager.list_events(session_id)])

    def test_snapshot_restore_preserves_session_and_events(self) -> None:
        manager = self._manager()
        manager.run_dry_run(self._order(), self._guard(), self._confirmations())
        snapshot = manager.snapshot()

        restored = self._manager()
        restored.restore(snapshot)
        session_id = restored.list_sessions()[0].session_id

        self.assertEqual("READY", restored.list_sessions()[0].status)
        self.assertEqual(["SESSION_CREATED", "SESSION_READY"], [event["event_type"] for event in restored.list_events(session_id)])

    def test_list_sessions_list_events_and_summary(self) -> None:
        manager = self._manager()
        manager.run_dry_run(self._order(), self._guard(), self._confirmations())

        sessions = manager.list_sessions()
        events = manager.list_events(sessions[0].session_id)
        summary = manager.summary()

        self.assertEqual(1, len(sessions))
        self.assertEqual(2, len(events))
        self.assertEqual(1, summary["lifecycle"]["context"]["state"]["session_count"])
        self.assertEqual(2, summary["lifecycle"]["context"]["journal"]["event_count"])

    def test_storage_runtime_queue_and_send_order_are_not_called(self) -> None:
        manager = self._manager()
        with (
            mock.patch.object(manager.storage, "commit") as storage_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = manager.run_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertEqual("READY", result["status"])
        storage_commit.assert_not_called()
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()

    def test_runtime_write_flags_remain_false(self) -> None:
        result = self._manager().run_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["dry_run_result"]["runtime_write"])
        self.assertFalse(result["context_adapter_result"]["runtime_write"])
        self.assertFalse(result["storage_commit_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["queue_commit_called"])

    def test_actual_runtime_files_and_rules_are_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        manager = self._manager()
        manager.run_dry_run(self._order(), self._guard(), self._confirmations())
        snapshot = manager.snapshot()
        manager.restore(snapshot)
        manager.summary()

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())

    def test_return_objects_are_deepcopy_isolated(self) -> None:
        manager = self._manager()

        result = manager.run_dry_run(self._order(), self._guard(), self._confirmations())
        result["dry_run_result"]["status"] = "MUTATED_RESULT"
        result["context_adapter_result"]["session_summary"]["status"] = "MUTATED_RESULT"
        sessions = manager.list_sessions()
        sessions[0].metadata["source"] = "MUTATED_SESSION_COPY"
        events = manager.list_events(sessions[0].session_id)
        events[0]["payload"]["status"] = "MUTATED_EVENT_COPY"

        session = manager.list_sessions()[0]
        event = manager.list_events(session.session_id)[0]
        self.assertEqual("READY", session.status)
        self.assertNotEqual("MUTATED_SESSION_COPY", session.metadata.get("source"))
        self.assertEqual("CREATED", event["payload"]["status"])


if __name__ == "__main__":
    unittest.main()
