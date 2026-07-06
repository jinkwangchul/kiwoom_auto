from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_lifecycle import ExecutionLifecycle
from execution_runtime_file_schema import default_order_executions_data, default_order_locks_data
from execution_runtime_manager import MANAGER_TYPE, ExecutionRuntimeManager
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


class ExecutionRuntimeManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.executions_path = self.root / "order_executions.json"
        self.locks_path = self.root / "order_locks.json"
        _write_json(self.executions_path, default_order_executions_data())
        _write_json(self.locks_path, default_order_locks_data())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _storage(self) -> ExecutionRuntimeStorage:
        return ExecutionRuntimeStorage(self.executions_path, self.locks_path)

    def _manager(self) -> ExecutionRuntimeManager:
        return ExecutionRuntimeManager(
            lifecycle=ExecutionLifecycle(metadata={"lifecycle": {"name": "test"}}),
            storage=self._storage(),
            metadata={"manager": {"name": "unit-test"}},
        )

    def _order(self) -> dict:
        return {
            "id": "ORDER_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_1",
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

    def test_run_dry_run_ready_creates_ready_session(self) -> None:
        manager = self._manager()

        result = manager.run_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertEqual(MANAGER_TYPE, result["manager_type"])
        self.assertEqual("READY", result["status"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual("READY", result["dry_run_result"]["status"])
        self.assertEqual("READY", result["context_adapter_result"]["status"])
        session = manager.list_sessions()[0]
        self.assertEqual("READY", session.status)

    def test_run_dry_run_blocked_creates_blocked_session(self) -> None:
        manager = self._manager()

        result = manager.run_dry_run(self._order(), self._guard(), confirmations={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("BLOCKED", result["dry_run_result"]["status"])
        self.assertEqual("BLOCKED", manager.list_sessions()[0].status)

    def test_run_dry_run_invalid_creates_invalid_session(self) -> None:
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

    def test_context_adapter_result_included(self) -> None:
        result = self._manager().run_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertIn("context_adapter_result", result)
        self.assertTrue(result["context_adapter_result"]["context_write"])
        self.assertTrue(result["context_adapter_result"]["session_id"].startswith("SESSION_EXEC_PREVIEW_ORDER_1_"))

    def test_snapshot_restore(self) -> None:
        manager = self._manager()
        manager.run_dry_run(self._order(), self._guard(), self._confirmations())
        snapshot = manager.snapshot()

        restored = self._manager()
        restored.restore(snapshot)

        self.assertEqual("READY", restored.list_sessions()[0].status)
        snapshot["context"]["state"]["sessions"][0]["status"] = "MUTATED_SNAPSHOT"
        self.assertEqual("READY", restored.list_sessions()[0].status)

    def test_list_sessions(self) -> None:
        manager = self._manager()
        manager.run_dry_run(self._order(), self._guard(), self._confirmations())

        sessions = manager.list_sessions()
        sessions[0].metadata["source"] = "mutated"

        self.assertEqual(1, len(sessions))
        self.assertNotEqual("mutated", manager.list_sessions()[0].metadata.get("source"))

    def test_list_events(self) -> None:
        manager = self._manager()
        manager.run_dry_run(self._order(), self._guard(), self._confirmations())
        session_id = manager.list_sessions()[0].session_id

        events = manager.list_events(session_id)

        self.assertEqual(["SESSION_CREATED", "SESSION_READY"], [event["event_type"] for event in events])
        events[0]["payload"]["status"] = "MUTATED"
        self.assertEqual("CREATED", manager.list_events(session_id)[0]["payload"]["status"])

    def test_summary(self) -> None:
        manager = self._manager()
        manager.run_dry_run(self._order(), self._guard(), self._confirmations())

        result = manager.summary()

        self.assertEqual(MANAGER_TYPE, result["manager_type"])
        self.assertTrue(result["in_memory"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(1, result["lifecycle"]["context"]["state"]["session_count"])
        self.assertEqual("unit-test", result["metadata"]["manager"]["name"])

    def test_storage_commit_send_order_queue_commit_not_called(self) -> None:
        manager = self._manager()
        with (
            mock.patch.object(manager.storage, "commit") as storage_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = manager.run_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertEqual("READY", result["status"])
        storage_commit.assert_not_called()
        send_order.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_constructor_validation_and_external_metadata_mutation(self) -> None:
        metadata = {"manager": {"name": "source"}}
        manager = ExecutionRuntimeManager(storage=self._storage(), metadata=metadata)
        metadata["manager"]["name"] = "mutated"

        self.assertEqual("source", manager.metadata["manager"]["name"])
        with self.assertRaises(ValueError):
            ExecutionRuntimeManager(lifecycle="bad", storage=self._storage())
        with self.assertRaises(ValueError):
            ExecutionRuntimeManager(storage="bad")
        with self.assertRaises(ValueError):
            ExecutionRuntimeManager(storage=self._storage(), metadata="bad")

    def test_no_runtime_storage_queue_gui_connections_beyond_declared_storage_type(self) -> None:
        import execution_runtime_manager

        module_text = execution_runtime_manager.__loader__.get_source(execution_runtime_manager.__name__)

        self.assertNotIn("commit_execution_runtime", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)

    def test_runtime_and_rules_hash_unchanged(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
