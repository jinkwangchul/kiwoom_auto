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
from execution_runtime_supervisor import ExecutionRuntimeSupervisor


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class ExecutionRuntimeSupervisorContractTest(unittest.TestCase):
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

    def _supervisor(self) -> ExecutionRuntimeSupervisor:
        return ExecutionRuntimeSupervisor(
            runtime_manager=ExecutionRuntimeManager(
                lifecycle=ExecutionLifecycle(),
                storage=self._storage(),
            )
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

    def test_run_ready_sets_last_result_ready(self) -> None:
        supervisor = self._supervisor()

        supervisor.run(self._order(), self._guard(), self._confirmations())

        self.assertEqual("READY", supervisor.last_result()["status"])

    def test_run_blocked_sets_last_result_blocked(self) -> None:
        supervisor = self._supervisor()

        supervisor.run(self._order(), self._guard(), confirmations={})

        self.assertEqual("BLOCKED", supervisor.last_result()["status"])

    def test_run_invalid_sets_last_result_invalid(self) -> None:
        supervisor = self._supervisor()
        with mock.patch.object(
            supervisor.runtime_manager,
            "run_dry_run",
            return_value={"status": "INVALID", "issues": ["FORCED_INVALID"], "warnings": []},
        ):
            supervisor.run(self._order(), self._guard(), self._confirmations())

        self.assertEqual("INVALID", supervisor.last_result()["status"])
        self.assertIn("FORCED_INVALID", supervisor.last_result()["issues"])

    def test_last_result_is_deepcopy(self) -> None:
        supervisor = self._supervisor()
        supervisor.run(self._order(), self._guard(), self._confirmations())

        last = supervisor.last_result()
        last["status"] = "MUTATED"
        last["dry_run_result"]["status"] = "MUTATED"

        self.assertEqual("READY", supervisor.last_result()["status"])
        self.assertEqual("READY", supervisor.last_result()["dry_run_result"]["status"])

    def test_clear_last_result(self) -> None:
        supervisor = self._supervisor()
        supervisor.run(self._order(), self._guard(), self._confirmations())

        supervisor.clear_last_result()

        self.assertIsNone(supervisor.last_result())

    def test_snapshot_restore_delegates_to_runtime_manager(self) -> None:
        supervisor = self._supervisor()
        snapshot_payload = {"snapshot_type": "EXECUTION_CONTEXT_SNAPSHOT", "context": {}}
        restore_payload = {"restored": True}

        with (
            mock.patch.object(supervisor.runtime_manager, "snapshot", return_value=snapshot_payload) as snapshot,
            mock.patch.object(supervisor.runtime_manager, "restore", return_value=restore_payload) as restore,
        ):
            snapshot_result = supervisor.snapshot()
            restore_result = supervisor.restore(snapshot_payload)

        snapshot.assert_called_once_with()
        restore.assert_called_once_with(snapshot_payload)
        self.assertEqual(snapshot_payload, snapshot_result)
        self.assertEqual(restore_payload, restore_result)

    def test_list_sessions_list_events_summary(self) -> None:
        supervisor = self._supervisor()
        supervisor.run(self._order(), self._guard(), self._confirmations())
        session_id = supervisor.list_sessions()[0].session_id

        self.assertEqual(1, len(supervisor.list_sessions()))
        self.assertEqual(["SESSION_CREATED", "SESSION_READY"], [event["event_type"] for event in supervisor.list_events(session_id)])
        self.assertEqual("READY", supervisor.summary()["last_status"])

    def test_commit_queue_send_order_and_gui_are_not_called(self) -> None:
        supervisor = self._supervisor()
        with (
            mock.patch.object(supervisor.runtime_manager.storage, "commit") as storage_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = supervisor.run(self._order(), self._guard(), self._confirmations())

        self.assertEqual("READY", result["status"])
        storage_commit.assert_not_called()
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()

    def test_supervisor_module_has_no_gui_or_commit_imports(self) -> None:
        import execution_runtime_supervisor

        module_text = execution_runtime_supervisor.__loader__.get_source(
            execution_runtime_supervisor.__name__
        )

        self.assertNotIn("commit_execution_runtime", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text.lower())
        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)
        self.assertNotIn("gui_", module_text)

    def test_runtime_write_flags_remain_false(self) -> None:
        supervisor = self._supervisor()

        result = supervisor.run(self._order(), self._guard(), self._confirmations())

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["dry_run_result"]["runtime_write"])
        self.assertFalse(supervisor.summary()["runtime_write"])

    def test_runtime_files_and_rules_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        supervisor = self._supervisor()
        supervisor.run(self._order(), self._guard(), self._confirmations())
        snapshot = supervisor.snapshot()
        supervisor.restore(snapshot)
        supervisor.summary()

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
