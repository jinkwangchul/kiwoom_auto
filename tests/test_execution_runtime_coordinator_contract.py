from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_lifecycle import ExecutionLifecycle
from execution_runtime_coordinator import COORDINATOR_TYPE, ExecutionRuntimeCoordinator
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


class ExecutionRuntimeCoordinatorContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.executions_path = root / "order_executions.json"
        self.locks_path = root / "order_locks.json"
        _write_json(self.executions_path, default_order_executions_data())
        _write_json(self.locks_path, default_order_locks_data())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _manager(self) -> ExecutionRuntimeManager:
        return ExecutionRuntimeManager(
            lifecycle=ExecutionLifecycle(),
            storage=ExecutionRuntimeStorage(self.executions_path, self.locks_path),
            metadata={"manager": {"name": "contract"}},
        )

    def _supervisor(self) -> ExecutionRuntimeSupervisor:
        return ExecutionRuntimeSupervisor(
            runtime_manager=self._manager(),
            metadata={"supervisor": {"name": "contract"}},
        )

    def _coordinator(self) -> ExecutionRuntimeCoordinator:
        return ExecutionRuntimeCoordinator(
            supervisor=self._supervisor(),
            metadata={"coordinator": {"name": "contract"}},
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

    def test_submit_ready_contract(self) -> None:
        coordinator = self._coordinator()

        result = coordinator.submit(self._order(), self._guard(), self._confirmations())

        self.assertEqual(COORDINATOR_TYPE, result["coordinator_type"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])

    def test_submit_blocked_contract(self) -> None:
        coordinator = self._coordinator()

        result = coordinator.submit(self._order(), self._guard(), confirmations={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])

    def test_submit_invalid_contract(self) -> None:
        coordinator = self._coordinator()
        with mock.patch.object(
            coordinator.supervisor,
            "run",
            return_value={
                "status": "INVALID",
                "issues": ["FORCED_INVALID"],
                "warnings": [],
            },
        ):
            result = coordinator.submit(self._order(), self._guard(), self._confirmations())

        self.assertEqual("INVALID", result["status"])
        self.assertIn("FORCED_INVALID", result["issues"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])

    def test_submit_executes_only_through_supervisor_run(self) -> None:
        coordinator = self._coordinator()
        order = self._order()
        guard = self._guard()
        confirmations = self._confirmations()

        with mock.patch.object(
            coordinator.supervisor,
            "run",
            wraps=coordinator.supervisor.run,
        ) as supervisor_run:
            result = coordinator.submit(order, guard, confirmations)

        self.assertEqual("READY", result["status"])
        supervisor_run.assert_called_once_with(order, guard, confirmations)

    def test_last_result_returns_deepcopy(self) -> None:
        coordinator = self._coordinator()
        coordinator.submit(self._order(), self._guard(), self._confirmations())

        first = coordinator.last_result()
        first["status"] = "MUTATED"
        first["supervisor_result"]["status"] = "MUTATED_SUPERVISOR"

        second = coordinator.last_result()
        self.assertEqual("READY", second["status"])
        self.assertEqual("READY", second["supervisor_result"]["status"])

    def test_clear_contract(self) -> None:
        coordinator = self._coordinator()
        coordinator.submit(self._order(), self._guard(), self._confirmations())

        coordinator.clear()

        self.assertIsNone(coordinator.last_result())
        self.assertIsNone(coordinator.supervisor.last_result())

    def test_snapshot_restore_contract(self) -> None:
        coordinator = self._coordinator()
        coordinator.submit(self._order(), self._guard(), self._confirmations())
        snapshot = coordinator.snapshot()

        restored = self._coordinator()
        restored.restore(snapshot)
        snapshot["context"]["state"]["sessions"][0]["status"] = "MUTATED"

        self.assertEqual("READY", restored.list_sessions()[0].status)
        self.assertEqual(
            ["SESSION_CREATED", "SESSION_READY"],
            [event["event_type"] for event in restored.list_events()],
        )

    def test_list_sessions_list_events_summary_contract(self) -> None:
        coordinator = self._coordinator()
        coordinator.submit(self._order(), self._guard(), self._confirmations())
        session = coordinator.list_sessions()[0]

        sessions = coordinator.list_sessions()
        events = coordinator.list_events(session.session_id)
        summary = coordinator.summary()

        sessions[0].metadata["mutated"] = True
        events[0]["payload"]["status"] = "MUTATED"

        self.assertEqual(1, len(sessions))
        self.assertEqual("READY", session.status)
        self.assertEqual(["SESSION_CREATED", "SESSION_READY"], [event["event_type"] for event in events])
        self.assertTrue(summary["has_last_result"])
        self.assertEqual("READY", summary["last_status"])
        self.assertNotIn("mutated", coordinator.list_sessions()[0].metadata)
        self.assertEqual("CREATED", coordinator.list_events(session.session_id)[0]["payload"]["status"])

    def test_commit_queue_send_order_paths_not_called_contract(self) -> None:
        coordinator = self._coordinator()
        with (
            mock.patch.object(coordinator.supervisor.runtime_manager.storage, "commit") as storage_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = coordinator.submit(self._order(), self._guard(), self._confirmations())

        self.assertEqual("READY", result["status"])
        storage_commit.assert_not_called()
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()

    def test_gui_queue_send_order_runtime_connections_absent_contract(self) -> None:
        import execution_runtime_coordinator

        module_text = execution_runtime_coordinator.__loader__.get_source(
            execution_runtime_coordinator.__name__
        )

        self.assertNotIn("commit_execution_runtime", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text.lower())
        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)
        self.assertNotIn("gui_", module_text.lower())

    def test_runtime_files_and_rules_unchanged_contract(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        coordinator = self._coordinator()
        coordinator.submit(self._order(), self._guard(), self._confirmations())
        coordinator.submit(self._order(), self._guard(), confirmations={})
        snapshot = coordinator.snapshot()
        coordinator.restore(snapshot)
        coordinator.summary()
        coordinator.clear()

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
